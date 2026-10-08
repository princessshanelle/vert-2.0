// Package ws is the gateway's live-push side of telemetry: a small hub
// that turns every reading already flowing through RabbitMQ into an
// immediate WebSocket message, so the dashboard's charts move the
// instant a device publishes instead of waiting for the next HTTP poll.
// It sits next to the existing history API rather than replacing it --
// a dropped/reconnecting socket should never mean a blank chart, so the
// frontend keeps polling for the source-of-truth history and treats the
// WebSocket purely as a low-latency nudge.
package ws

import (
	"context"
	"encoding/json"
	"log"
	"time"
)

// Message is one metric update, broadcast to every connected dashboard
// regardless of which device/zone it's looking at -- a household's worth
// of sensors is small enough that per-client filtering isn't worth the
// complexity; the frontend just ignores updates for a device it isn't
// currently charting.
type Message struct {
	DeviceID string    `json:"device_id"`
	Metric   string    `json:"metric"`
	Time     time.Time `json:"t"`
	Value    float64   `json:"v"`
}

// outboxSize bounds how far a slow client can lag before the hub gives up
// on it. A dashboard tab that's been backgrounded/suspended by the
// browser is exactly the kind of client this protects the rest of the
// system from -- without a cap, one stuck client's TCP buffer backs up
// the shared broadcast channel and stalls every other viewer.
const outboxSize = 32

type client struct {
	send chan []byte
}

// Hub fans out broadcasts to every registered client. All state changes
// go through the single Run goroutine's select loop rather than a mutex,
// so registration/unregistration and broadcast can never race each other.
type Hub struct {
	register   chan *client
	unregister chan *client
	broadcast  chan []byte
	clients    map[*client]bool
}

func NewHub() *Hub {
	return &Hub{
		register:   make(chan *client),
		unregister: make(chan *client),
		broadcast:  make(chan []byte, 256),
		clients:    make(map[*client]bool),
	}
}

// Run owns the hub's state for the lifetime of ctx. Call it once, in its
// own goroutine, before any handler starts registering clients.
func (h *Hub) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			for c := range h.clients {
				close(c.send)
			}
			return
		case c := <-h.register:
			h.clients[c] = true
		case c := <-h.unregister:
			if _, ok := h.clients[c]; ok {
				delete(h.clients, c)
				close(c.send)
			}
		case payload := <-h.broadcast:
			for c := range h.clients {
				select {
				case c.send <- payload:
				default:
					// Client's outbox is full -- it's not keeping up.
					// Drop it rather than block every other viewer on
					// one slow socket; the frontend's own reconnect
					// logic (and its polling fallback) takes it from
					// there.
					delete(h.clients, c)
					close(c.send)
				}
			}
		}
	}
}

// Broadcast pushes one reading to every connected client. Safe to call
// from the AMQP consumer goroutine concurrently with Run's loop.
func (h *Hub) Broadcast(msg Message) {
	payload, err := json.Marshal(msg)
	if err != nil {
		log.Printf("ws: could not marshal broadcast message: %v", err)
		return
	}
	select {
	case h.broadcast <- payload:
	default:
		// The hub itself is backed up (e.g. no Run goroutine started,
		// or an extreme burst) -- drop rather than block telemetry
		// ingestion, which must keep writing to TimescaleDB regardless.
		log.Printf("ws: broadcast channel full, dropping update for %s/%s", msg.DeviceID, msg.Metric)
	}
}
