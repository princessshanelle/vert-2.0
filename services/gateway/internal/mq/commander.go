package mq

import (
	"encoding/json"
	"errors"
	"sync"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Commander lets an HTTP handler send a one-off command to a device --
// "open," "close," "start streaming" -- the manual-control counterpart
// to the automation engine's rule-triggered commands (see
// services/automation/internal/mq, which publishes the same shape for
// the same reason). It holds a channel that gets swapped out by
// runMessaging on every reconnect, so a handler never has to know or
// care whether RabbitMQ dropped and came back five seconds ago.
type Commander struct {
	mu sync.RWMutex
	ch *amqp.Channel
}

func NewCommander() *Commander {
	return &Commander{}
}

// Attach gives the commander a fresh channel on the current connection.
// Called once per reconnect in runMessaging.
func (c *Commander) Attach(conn *amqp.Connection) error {
	ch, err := conn.Channel()
	if err != nil {
		return err
	}
	c.mu.Lock()
	c.ch = ch
	c.mu.Unlock()
	return nil
}

var ErrNotConnected = errors.New("not connected to rabbitmq right now")

// Send publishes a command to one device. zone/deviceType come from the
// device's own registration (Section 4.5) so the routing key is built
// the same way everywhere, never typed out ad hoc per caller.
func (c *Commander) Send(zone, deviceType, deviceID string, payload map[string]any) error {
	c.mu.RLock()
	ch := c.ch
	c.mu.RUnlock()
	if ch == nil {
		return ErrNotConnected
	}

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
	return ch.Publish(DeviceExchange, routingKey, false, false, amqp.Publishing{
		ContentType: "application/json",
		Body:        body,
	})
}
