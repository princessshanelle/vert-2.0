package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/vert-project/gateway/internal/store"
)

type fakeZones map[string][2]string // device id -> {type, zone}

func (f fakeZones) DeviceTypeAndZone(_ context.Context, id string) (string, string, error) {
	v, ok := f[id]
	if !ok {
		return "", "", store.ErrNotFound
	}
	return v[0], v[1], nil
}

func topicDecision(t *testing.T, h *RabbitMQAuthHandler, username, routingKey, permission string) string {
	t.Helper()
	form := url.Values{"username": {username}, "name": {"amq.topic"}, "routing_key": {routingKey}, "permission": {permission}}
	req := httptest.NewRequest(http.MethodPost, "/internal/rabbitmq/auth/topic", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	rec := httptest.NewRecorder()
	h.Topic(rec, req)
	return rec.Body.String()
}

func TestTopicAuthorization(t *testing.T) {
	h := &RabbitMQAuthHandler{Zones: fakeZones{
		"plantcam-01": {"ai-camera", "zoneA"},
		"dht22-01":    {"temp-humidity", "zoneA"},
		"cam-nozone":  {"ai-camera", ""},
	}}
	cases := []struct {
		name, user, key, perm, want string
	}{
		{"own topic write", "dht22-01", "vert.zoneA.temp-humidity.dht22-01.reading", "write", "allow"},
		{"own cmd subscribe", "plantcam-01", "vert.*.ai-camera.plantcam-01.cmd", "read", "allow"},
		{"spoof another device", "dht22-01", "vert.zoneA.temp-humidity.dht22-02.reading", "write", "deny"},
		{"sensor may not read others", "dht22-01", "vert.zoneA.*.*.reading", "read", "deny"},
		{"ai camera reads own zone readings", "plantcam-01", "vert.zoneA.*.*.reading", "read", "allow"},
		{"ai camera may not write others' readings", "plantcam-01", "vert.zoneA.temp-humidity.dht22-01.reading", "write", "deny"},
		{"ai camera may not read another zone", "plantcam-01", "vert.zoneB.*.*.reading", "read", "deny"},
		{"ai camera may not wildcard the zone", "plantcam-01", "vert.*.*.*.reading", "read", "deny"},
		{"ai camera may not read commands", "plantcam-01", "vert.zoneA.window.window01.cmd", "read", "deny"},
		{"ai camera with no zone", "cam-nozone", "vert.zoneA.*.*.reading", "read", "deny"},
		{"unknown device", "ghost", "vert.zoneA.*.*.reading", "read", "deny"},
		{"not a vert topic", "plantcam-01", "other.zoneA.x.plantcam-01.reading", "read", "deny"},
	}
	for _, c := range cases {
		if got := topicDecision(t, h, c.user, c.key, c.perm); got != c.want {
			t.Errorf("%s: got %s, want %s", c.name, got, c.want)
		}
	}
}

func TestTopicAuthorizationWithoutLookupKeepsOldBehaviour(t *testing.T) {
	h := &RabbitMQAuthHandler{}
	if got := topicDecision(t, h, "plantcam-01", "vert.zoneA.*.*.reading", "read"); got != "deny" {
		t.Fatalf("got %s", got)
	}
	if got := topicDecision(t, h, "plantcam-01", "vert.zoneA.ai-camera.plantcam-01.reading", "write"); got != "allow" {
		t.Fatalf("got %s", got)
	}
}
