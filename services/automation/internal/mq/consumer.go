package mq

import (
	"context"
	"encoding/json"
	"log"

	amqp "github.com/rabbitmq/amqp091-go"
)

// RunEvaluationConsumer feeds every telemetry reading to onReading. It
// mirrors the gateway's ingestion consumer (same dead-lettering approach
// for messages it can't parse) rather than sharing code with it -- see
// the architecture doc on why each service owns its own copy of this
// small amount of plumbing.
func RunEvaluationConsumer(ctx context.Context, conn *amqp.Connection, onReading func(context.Context, Envelope)) {
	ch, err := conn.Channel()
	if err != nil {
		log.Printf("automation consumer: open channel: %v", err)
		return
	}
	defer ch.Close()

	msgs, err := ch.Consume(QueueRuleEvaluation, "automation.rules", false, false, false, false, nil)
	if err != nil {
		log.Printf("automation consumer: consume: %v", err)
		return
	}

	for {
		select {
		case <-ctx.Done():
			return
		case d, ok := <-msgs:
			if !ok {
				return
			}
			var env Envelope
			if err := json.Unmarshal(d.Body, &env); err != nil || env.SchemaVersion != CurrentSchemaVersion {
				_ = d.Nack(false, false)
				continue
			}
			onReading(ctx, env)
			_ = d.Ack(false)
		}
	}
}
