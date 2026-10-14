package api

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/vert-project/gateway/internal/store"
)

// RabbitMQAuthHandler implements the four endpoints RabbitMQ's
// rabbitmq_auth_backend_http plugin calls for anyone the internal
// (Mnesia) user database doesn't recognise -- which, by design, is every
// device (see infra/rabbitmq/definitions.json). This is the mechanism
// that makes per-device credentials and topic-scoped permissions real:
// a device can only ever publish or subscribe under its own device_id
// segment of the topic namespace, so a compromised or misbehaving sensor
// cannot see or spoof another one's data.
//
// These routes are only ever meant to be reachable from RabbitMQ itself,
// on the private Docker network -- they are intentionally not behind
// user JWT auth, since RabbitMQ has no JWT to send. Do not expose the
// gateway's HTTP port to anything other than the docker-compose network
// and the LAN clients that need /api/v1.
//
// Contract reference: https://github.com/rabbitmq/rabbitmq-auth-backend-http
type RabbitMQAuthHandler struct {
	DB *store.DB
	// Zones answers "what type is this device, and which zone is it in?"
	// for the one cross-device permission below. *store.DB implements it;
	// tests substitute a fake. Nil disables that permission entirely.
	Zones deviceZoneLookup

	cacheMu sync.Mutex
	cache   map[string]zoneCacheEntry
}

type deviceZoneLookup interface {
	DeviceTypeAndZone(ctx context.Context, deviceID string) (deviceType, zone string, err error)
}

type zoneCacheEntry struct {
	deviceType, zone string
	expires          time.Time
}

// zoneReaderTypes are device types allowed to READ (never write) the
// `reading` topics of other devices in their own zone. An on-device AI
// camera (edge/plant-cam) uses the zone's temperature/humidity to judge
// disease pressure; it still can't publish as, or command, anything else.
var zoneReaderTypes = map[string]bool{"ai-camera": true}

const zoneCacheTTL = 60 * time.Second

func allow(w http.ResponseWriter) {
	w.Write([]byte("allow"))
}

func deny(w http.ResponseWriter) {
	w.Write([]byte("deny"))
}

// User: is this username/password a real, non-revoked device credential?
func (h *RabbitMQAuthHandler) User(w http.ResponseWriter, r *http.Request) {
	username := r.FormValue("username")
	password := r.FormValue("password")
	if username == "" || password == "" {
		deny(w)
		return
	}
	ok, err := h.DB.VerifyDeviceCredential(r.Context(), username, password)
	if err != nil || !ok {
		deny(w)
		return
	}
	allow(w)
}

// Vhost: devices only ever need the default vhost.
func (h *RabbitMQAuthHandler) Vhost(w http.ResponseWriter, r *http.Request) {
	if r.FormValue("vhost") == "/" {
		allow(w)
		return
	}
	deny(w)
}

// Resource: a device may touch the shared MQTT topic exchange, and the
// per-connection queue the MQTT plugin creates on its own behalf (named
// after the MQTT client id, which our firmware always sets to the
// device's own id) -- nothing else.
func (h *RabbitMQAuthHandler) Resource(w http.ResponseWriter, r *http.Request) {
	username := r.FormValue("username")
	resource := r.FormValue("resource")
	name := r.FormValue("name")

	switch resource {
	case "exchange":
		if name == "amq.topic" {
			allow(w)
			return
		}
	case "queue":
		if strings.HasPrefix(name, "mqtt-subscription-"+username) {
			allow(w)
			return
		}
	}
	deny(w)
}

// Topic: the routing key's device_id segment must match the
// authenticated username -- with one read-only exception, see
// mayReadZoneReadings. Routing keys follow
// vert.<zone>.<device_type>.<device_id>.<kind> (RabbitMQ's MQTT
// plugin converts the "/" a device used over MQTT into "." here), so
// this is a pure string check -- no database round trip on the hot path
// of every single message a device publishes or subscribes to.
func (h *RabbitMQAuthHandler) Topic(w http.ResponseWriter, r *http.Request) {
	username := r.FormValue("username")
	name := r.FormValue("name")
	routingKey := r.FormValue("routing_key")

	if name != "amq.topic" {
		deny(w)
		return
	}
	segments := strings.Split(routingKey, ".")
	if len(segments) < 4 || segments[0] != "vert" {
		deny(w)
		return
	}
	if segments[3] == username {
		allow(w)
		return
	}
	if r.FormValue("permission") == "read" && h.mayReadZoneReadings(r.Context(), username, segments) {
		allow(w)
		return
	}
	deny(w)
}

// mayReadZoneReadings: vert.<zone>.<type>.<id>.reading, where <zone> is the
// caller's own (concrete, not wildcarded) zone and the caller is a zone
// reader type. The answer is cached briefly: RabbitMQ asks on every
// subscribe, and a zone reassignment taking a minute to apply is fine.
func (h *RabbitMQAuthHandler) mayReadZoneReadings(ctx context.Context, username string, segments []string) bool {
	if h.Zones == nil || len(segments) != 5 || segments[4] != "reading" {
		return false
	}
	zone := segments[1]
	if zone == "" || zone == "*" || zone == "#" {
		return false
	}
	deviceType, deviceZone, ok := h.lookup(ctx, username)
	return ok && zoneReaderTypes[deviceType] && deviceZone != "" && deviceZone == zone
}

func (h *RabbitMQAuthHandler) lookup(ctx context.Context, deviceID string) (string, string, bool) {
	now := time.Now()
	h.cacheMu.Lock()
	if e, ok := h.cache[deviceID]; ok && now.Before(e.expires) {
		h.cacheMu.Unlock()
		return e.deviceType, e.zone, true
	}
	h.cacheMu.Unlock()

	deviceType, zone, err := h.Zones.DeviceTypeAndZone(ctx, deviceID)
	if err != nil {
		return "", "", false
	}
	h.cacheMu.Lock()
	if h.cache == nil {
		h.cache = make(map[string]zoneCacheEntry)
	}
	h.cache[deviceID] = zoneCacheEntry{deviceType: deviceType, zone: zone, expires: now.Add(zoneCacheTTL)}
	h.cacheMu.Unlock()
	return deviceType, zone, true
}
