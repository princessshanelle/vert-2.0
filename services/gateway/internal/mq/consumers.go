package mq

import (
	"context"
	"encoding/json"
	"log"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/vert-project/gateway/internal/store"
	"github.com/vert-project/gateway/internal/timeseries"
	"github.com/vert-project/gateway/internal/ws"
)

// RunTelemetryConsumer refreshes the publishing device's "last seen"
// status, remembers its most recent reading (store.UpdateDeviceReading,
// what the dashboard's cards show right now), records every numeric
// field into TimescaleDB (timeseries.InsertReading, what the dashboard's
// charts are drawn from), and pushes the same numeric fields out over
// the WebSocket hub so a connected dashboard sees the point immediately
// instead of waiting for its next history poll. It is deliberately dumb
// otherwise: no thresholds, no rules -- that belongs to the automation
// service, which subscribes to the same exchange independently.
func RunTelemetryConsumer(ctx context.Context, conn *amqp.Connection, db *store.DB, ts *timeseries.DB, hub *ws.Hub) {
	ch, err := conn.Channel()
	if err != nil {
		log.Printf("telemetry consumer: open channel: %v", err)
		return
	}
	defer ch.Close()

	msgs, err := ch.Consume(QueueTelemetryIngestion, "gateway.telemetry", false, false, false, false, nil)
	if err != nil {
		log.Printf("telemetry consumer: consume: %v", err)
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
			if err := json.Unmarshal(d.Body, &env); err != nil {
				log.Printf("telemetry consumer: bad envelope, dead-lettering: %v", err)
				_ = d.Nack(false, false) // routed to the DLX, not requeued
				continue
			}
			if env.SchemaVersion != CurrentSchemaVersion {
				log.Printf("telemetry consumer: unsupported schema %q from %s", env.SchemaVersion, env.DeviceID)
				_ = d.Nack(false, false)
				continue
			}
			payload, err := json.Marshal(env.Payload)
			if err != nil {
				log.Printf("telemetry consumer: could not re-marshal payload from %s: %v", env.DeviceID, err)
			} else if err := db.UpdateDeviceReading(ctx, env.DeviceID, payload); err != nil {
				log.Printf("telemetry consumer: update reading: %v", err)
			}
			readingTime := env.Timestamp
			if readingTime.IsZero() {
				readingTime = time.Now().UTC()
			}
			if err := ts.InsertReading(ctx, env.DeviceID, readingTime, env.Payload); err != nil {
				log.Printf("telemetry consumer: insert time-series reading: %v", err)
			}
			// Broadcast independently of the TimescaleDB write above: a
			// slow or briefly-unavailable database should never hold up
			// the live push, and a dropped broadcast (no clients, full
			// buffer) should never hold up the insert. The frontend's
			// periodic history poll is the source of truth either way.
			for metric, raw := range env.Payload {
				value, ok := numericValue(raw)
				if !ok {
					continue
				}
				hub.Broadcast(ws.Message{
					DeviceID: env.DeviceID,
					Metric:   metric,
					Time:     readingTime,
					Value:    value,
				})
			}
			_ = d.Ack(false)
		}
	}
}

// RunLifecycleConsumer handles device registration announcements and
// online/offline (LWT) events published to the events exchange.
func RunLifecycleConsumer(ctx context.Context, conn *amqp.Connection, db *store.DB) {
	ch, err := conn.Channel()
	if err != nil {
		log.Printf("lifecycle consumer: open channel: %v", err)
		return
	}
	defer ch.Close()

	msgs, err := ch.Consume(QueueLifecycleEvents, "gateway.lifecycle", false, false, false, false, nil)
	if err != nil {
		log.Printf("lifecycle consumer: consume: %v", err)
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
			var evt struct {
				Kind         string          `json:"kind"` // "announce" | "online" | "offline"
				DeviceID     string          `json:"device_id"`
				DeviceType   string          `json:"device_type"`
				Capabilities json.RawMessage `json:"capabilities"`
				IP           string          `json:"ip"` // optional: the device's own LAN address, for live-view links
			}
			if err := json.Unmarshal(d.Body, &evt); err != nil {
				_ = d.Nack(false, false)
				continue
			}
			switch evt.Kind {
			case "announce":
				if err := db.UpsertDevice(ctx, evt.DeviceID, evt.DeviceType, evt.Capabilities, evt.IP); err != nil {
					log.Printf("lifecycle consumer: upsert device: %v", err)
				}
			case "online", "offline":
				if err := db.MarkDeviceStatus(ctx, evt.DeviceID, evt.Kind); err != nil {
					log.Printf("lifecycle consumer: mark status: %v", err)
				}
			}
			_ = d.Ack(false)
		}
	}
}

// numericValue extracts a chartable float64 out of a decoded JSON payload
// field, mirroring timeseries.toFloat: a payload can carry non-numeric
// fields (an AI result's label string, say) that simply aren't
// broadcastable points, not an error condition.
func numericValue(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	}
	return 0, false
}
