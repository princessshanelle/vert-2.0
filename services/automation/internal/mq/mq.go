package mq

import (
	"encoding/json"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

// DeviceExchange is amq.topic -- the one exchange RabbitMQ's MQTT plugin
// ever publishes device messages to or delivers device subscriptions
// from (see infra/rabbitmq/rabbitmq.conf's mqtt.exchange setting, and the
// identical comment in services/gateway/internal/mq/mq.go). A command
// published anywhere else would never reach a device subscribed over
// MQTT, no matter how correct the routing key looked.
const DeviceExchange = "amq.topic"

const QueueRuleEvaluation = "q.automation.rules"

func Connect(url string) (*amqp.Connection, error) {
	return amqp.Dial(url)
}

// DeclareTopology only declares the one queue this service owns.
// amq.topic itself is one of RabbitMQ's built-in exchanges and always
// exists -- there is nothing to declare for it.
func DeclareTopology(conn *amqp.Connection) error {
	ch, err := conn.Channel()
	if err != nil {
		return err
	}
	defer ch.Close()

	if _, err := ch.QueueDeclare(QueueRuleEvaluation, true, false, false, false, nil); err != nil {
		return err
	}
	return ch.QueueBind(QueueRuleEvaluation, "vert.#.reading", DeviceExchange, false, nil)
}

// Envelope mirrors services/gateway/internal/mq/envelope.go. Three
// independent copies of this shape now exist (Go gateway, Go automation,
// Python inference) by design -- see the architecture doc on why services
// agree on the JSON contract rather than sharing code across languages.
type Envelope struct {
	SchemaVersion string         `json:"schema_version"`
	DeviceID      string         `json:"device_id"`
	DeviceType    string         `json:"device_type"`
	Zone          string         `json:"zone"`
	Timestamp     time.Time      `json:"ts"`
	Sequence      uint64         `json:"seq"`
	Payload       map[string]any `json:"payload"`
}

const CurrentSchemaVersion = "1.0"

type Publisher struct {
	ch *amqp.Channel
}

func NewPublisher(conn *amqp.Connection) (*Publisher, error) {
	ch, err := conn.Channel()
	if err != nil {
		return nil, err
	}
	return &Publisher{ch: ch}, nil
}

// PublishCommand tells a device to do something -- e.g. open a vent --
// wrapped in the same envelope shape as a sensor reading, so nothing
// downstream needs a second parser for "things a device receives" versus
// "things a device sends".
func (p *Publisher) PublishCommand(zone, deviceType, deviceID string, payload map[string]any) error {
	body, err := json.Marshal(Envelope{
		SchemaVersion: CurrentSchemaVersion,
		DeviceID:      deviceID,
		DeviceType:    deviceType,
		Zone:          zone,
		Timestamp:     time.Now().UTC(),
		Payload:       payload,
	})
	if err != nil {
		return err
	}
	routingKey := "vert." + zone + "." + deviceType + "." + deviceID + ".cmd"
	return p.ch.Publish(DeviceExchange, routingKey, false, false, amqp.Publishing{
		ContentType: "application/json",
		Body:        body,
	})
}

// PublishAlertEvent puts the same alert that was written to Postgres onto
// the bus, so a future notification service (or the dashboard, over a
// live connection) can react to it in real time without polling the
// database. See docs "What's missing" -- nothing subscribes to this yet,
// but the alert is not lost either way, since it's also persisted.
func (p *Publisher) PublishAlertEvent(zone, deviceType, deviceID, message string) error {
	body, err := json.Marshal(map[string]any{
		"kind":      "alert",
		"device_id": deviceID,
		"message":   message,
		"ts":        time.Now().UTC(),
	})
	if err != nil {
		return err
	}
	routingKey := "vert." + zone + "." + deviceType + "." + deviceID + ".alert"
	return p.ch.Publish(DeviceExchange, routingKey, false, false, amqp.Publishing{
		ContentType: "application/json",
		Body:        body,
	})
}
