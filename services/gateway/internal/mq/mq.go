package mq

import amqp "github.com/rabbitmq/amqp091-go"

func Connect(url string) (*amqp.Connection, error) {
	return amqp.Dial(url)
}

// DeviceExchange is amq.topic -- RabbitMQ's built-in topic exchange, and
// critically the SAME exchange the MQTT plugin publishes every device
// message to (see infra/rabbitmq/rabbitmq.conf's mqtt.exchange setting).
// RabbitMQ's MQTT plugin has no concept of routing different MQTT topics
// to different backing exchanges -- every device, on every topic, always
// publishes to this one exchange. Anything meant to be reachable from a
// device (telemetry readings, lifecycle announce/offline, camera frame
// events, and commands going back out to a device) has to live here, not
// on a separately-declared exchange no MQTT message will ever reach.
// Backend-only traffic (nothing here yet) could use its own exchange.
const DeviceExchange = "amq.topic"

const (
	QueueTelemetryIngestion = "q.ingestion.telemetry"
	QueueLifecycleEvents    = "q.registry.lifecycle"

	deadLetterExchange = "vert.dlx"
)

// DeclareTopology creates the queues/bindings the gateway owns, against
// the shared DeviceExchange. It is safe to call on every boot: all
// declarations are idempotent. amq.topic itself always exists already --
// it's one of RabbitMQ's standard built-in exchanges in every vhost, so
// there's nothing to declare for it.
func DeclareTopology(conn *amqp.Connection) error {
	ch, err := conn.Channel()
	if err != nil {
		return err
	}
	defer ch.Close()

	dlx := amqp.Table{"x-dead-letter-exchange": deadLetterExchange}
	if err := ch.ExchangeDeclare(deadLetterExchange, "fanout", true, false, false, false, nil); err != nil {
		return err
	}

	if _, err := ch.QueueDeclare(QueueTelemetryIngestion, true, false, false, false, dlx); err != nil {
		return err
	}
	if err := ch.QueueBind(QueueTelemetryIngestion, "vert.#.reading", DeviceExchange, false, nil); err != nil {
		return err
	}

	if _, err := ch.QueueDeclare(QueueLifecycleEvents, true, false, false, false, dlx); err != nil {
		return err
	}
	return ch.QueueBind(QueueLifecycleEvents, "vert.#", DeviceExchange, false, nil)
}
