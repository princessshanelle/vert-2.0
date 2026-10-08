// Package engine evaluates VERT's automation rules against incoming
// telemetry and turns a sustained breach into a device command plus a
// human-readable alert. It is deliberately the only place in the system
// that decides "this has gone on long enough to act on" -- ingestion
// stays a dumb pipe (see services/gateway), so this is where that
// judgment call lives, in one auditable place.
package engine

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/vert-project/automation/internal/mq"
	"github.com/vert-project/automation/internal/store"
)

type Engine struct {
	db        *store.DB
	publisher *mq.Publisher

	mu        sync.Mutex
	rules     []store.Rule
	breaches  map[string]*breachState // key: ruleID + "|" + deviceID
	deviceTTL map[string]cachedDevice
}

type breachState struct {
	startedAt time.Time
	fired     bool
}

type cachedDevice struct {
	info      *store.DeviceInfo
	expiresAt time.Time
}

func New(db *store.DB, publisher *mq.Publisher) *Engine {
	return &Engine{
		db:        db,
		publisher: publisher,
		breaches:  make(map[string]*breachState),
		deviceTTL: make(map[string]cachedDevice),
	}
}

// SetPublisher swaps the channel the engine publishes commands and alert
// events on. Called after every reconnect (see cmd/automation/main.go's
// runMessaging) so a dropped RabbitMQ connection doesn't leave the engine
// holding a dead channel forever -- it just starts using the new one.
func (e *Engine) SetPublisher(p *mq.Publisher) {
	e.mu.Lock()
	e.publisher = p
	e.mu.Unlock()
}

func (e *Engine) getPublisher() *mq.Publisher {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.publisher
}

// RefreshRules is called on a timer (see cmd/automation/main.go). Rules
// are a handful of rows a person edits occasionally, so polling every
// 30s is simpler and just as correct as event-driven invalidation here.
func (e *Engine) RefreshRules(ctx context.Context) {
	rules, err := e.db.ListEnabledRules(ctx)
	if err != nil {
		log.Printf("automation: could not refresh rules: %v", err)
		return
	}
	e.mu.Lock()
	e.rules = rules
	e.mu.Unlock()
}

type condition struct {
	Metric     string  `json:"metric"`
	Op         string  `json:"op"`
	Value      float64 `json:"value"`
	ForMinutes float64 `json:"for_minutes"`
}

type action struct {
	DeviceID string `json:"device_id"`
	Command  string `json:"command"`
}

// Evaluate is called once per telemetry reading. It never blocks on a
// slow downstream (DB, broker) for more than one message's worth of
// work, and a failure evaluating one rule never stops the others from
// being checked -- one grower's misconfigured rule should not silently
// disable everyone else's.
func (e *Engine) Evaluate(ctx context.Context, env mq.Envelope) {
	e.mu.Lock()
	rules := append([]store.Rule(nil), e.rules...)
	e.mu.Unlock()

	device, err := e.deviceInfo(ctx, env.DeviceID)
	if err != nil {
		return // device not yet registered; nothing to evaluate against
	}

	for _, rule := range rules {
		applies := (rule.DeviceID != nil && *rule.DeviceID == env.DeviceID) ||
			(rule.ZoneID != nil && device.ZoneID != nil && *rule.ZoneID == *device.ZoneID)
		if !applies {
			continue
		}
		e.evaluateRule(ctx, rule, env, device)
	}
}

func (e *Engine) evaluateRule(ctx context.Context, rule store.Rule, env mq.Envelope, device *store.DeviceInfo) {
	var cond condition
	if err := unmarshal(rule.Condition, &cond); err != nil {
		log.Printf("automation: rule %s has an unreadable condition: %v", rule.ID, err)
		return
	}

	raw, ok := env.Payload[cond.Metric]
	if !ok {
		return
	}
	value, ok := toFloat(raw)
	if !ok {
		return
	}

	breaching := compare(value, cond.Op, cond.Value)
	key := rule.ID + "|" + env.DeviceID

	e.mu.Lock()
	state, tracked := e.breaches[key]
	if !breaching {
		delete(e.breaches, key) // condition cleared: forget the episode, allow it to fire again next time
		e.mu.Unlock()
		return
	}
	if !tracked {
		state = &breachState{startedAt: time.Now()}
		e.breaches[key] = state
	}
	sustainedFor := time.Since(state.startedAt)
	alreadyFired := state.fired
	if sustainedFor >= time.Duration(cond.ForMinutes*float64(time.Minute)) && !alreadyFired {
		state.fired = true
	}
	shouldFire := state.fired && !alreadyFired
	e.mu.Unlock()

	if !shouldFire {
		return
	}
	e.fire(ctx, rule, env, device, cond)
}

func (e *Engine) fire(ctx context.Context, rule store.Rule, env mq.Envelope, sourceDevice *store.DeviceInfo, cond condition) {
	var act action
	if err := unmarshal(rule.Action, &act); err != nil {
		log.Printf("automation: rule %s has an unreadable action: %v", rule.ID, err)
		return
	}

	targetDevice, err := e.deviceInfo(ctx, act.DeviceID)
	if err != nil {
		log.Printf("automation: rule %q wants to command unknown device %q", rule.Name, act.DeviceID)
		return
	}

	publisher := e.getPublisher()
	if err := publisher.PublishCommand(targetDevice.ZoneSlug, targetDevice.DeviceType, act.DeviceID, map[string]any{
		"command": act.Command,
		"source":  "rule:" + rule.ID,
	}); err != nil {
		log.Printf("automation: could not publish command for rule %q: %v", rule.Name, err)
		return
	}

	message := friendlyMessage(rule.Name, env.DeviceID, cond, act)
	ruleID := rule.ID
	if err := e.db.CreateAlert(ctx, env.DeviceID, &ruleID, "warning", message); err != nil {
		log.Printf("automation: could not record alert for rule %q: %v", rule.Name, err)
	}
	if err := publisher.PublishAlertEvent(sourceDevice.ZoneSlug, sourceDevice.DeviceType, env.DeviceID, message); err != nil {
		log.Printf("automation: could not publish alert event for rule %q: %v", rule.Name, err)
	}

	log.Printf("automation: %s", message)
}

// friendlyMessage is written for whoever actually reads the alert feed --
// often someone checking their phone between other jobs, not an engineer
// reading a log. It says what happened and what VERT is doing about it,
// in one sentence, in plain language.
func friendlyMessage(ruleName, deviceID string, cond condition, act action) string {
	return fmt.Sprintf(
		"%s: %s has read %s %.1f for over %.0f minutes, so VERT is telling %s to %s.",
		ruleName, deviceID, cond.Metric, cond.Value, cond.ForMinutes, act.DeviceID, act.Command,
	)
}

func (e *Engine) deviceInfo(ctx context.Context, deviceID string) (*store.DeviceInfo, error) {
	e.mu.Lock()
	if cached, ok := e.deviceTTL[deviceID]; ok && time.Now().Before(cached.expiresAt) {
		e.mu.Unlock()
		return cached.info, nil
	}
	e.mu.Unlock()

	info, err := e.db.GetDeviceInfo(ctx, deviceID)
	if err != nil {
		return nil, err
	}

	e.mu.Lock()
	e.deviceTTL[deviceID] = cachedDevice{info: info, expiresAt: time.Now().Add(time.Minute)}
	e.mu.Unlock()
	return info, nil
}
