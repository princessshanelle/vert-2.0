package store

import (
	"context"
	"encoding/json"

	"github.com/google/uuid"
)

type Rule struct {
	ID        string          `json:"id"`
	ZoneID    *string         `json:"zone_id,omitempty"`
	DeviceID  *string         `json:"device_id,omitempty"`
	Name      string          `json:"name"`
	Condition json.RawMessage `json:"condition"`
	Action    json.RawMessage `json:"action"`
	Enabled   bool            `json:"enabled"`
}

// ListEnabledRules is polled periodically by the evaluation loop rather
// than driven by a "rule changed" event -- simple beats clever here, and
// a 30-second-stale rule set is an acceptable trade against the
// complexity of cache invalidation for something a person edits a
// handful of times a season, not per minute.
func (db *DB) ListEnabledRules(ctx context.Context) ([]Rule, error) {
	rows, err := db.Pool.Query(ctx, `
		select id, zone_id, device_id, name, condition, action, enabled
		from automation_rules where enabled = true
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Rule
	for rows.Next() {
		var r Rule
		if err := rows.Scan(&r.ID, &r.ZoneID, &r.DeviceID, &r.Name, &r.Condition, &r.Action, &r.Enabled); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (db *DB) ListAllRules(ctx context.Context) ([]Rule, error) {
	rows, err := db.Pool.Query(ctx, `
		select id, zone_id, device_id, name, condition, action, enabled
		from automation_rules order by created_at
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Rule
	for rows.Next() {
		var r Rule
		if err := rows.Scan(&r.ID, &r.ZoneID, &r.DeviceID, &r.Name, &r.Condition, &r.Action, &r.Enabled); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (db *DB) CreateRule(ctx context.Context, r Rule) (*Rule, error) {
	row := db.Pool.QueryRow(ctx, `
		insert into automation_rules (zone_id, device_id, name, condition, action, enabled)
		values ($1, $2, $3, $4, $5, coalesce($6, true))
		returning id, zone_id, device_id, name, condition, action, enabled
	`, r.ZoneID, r.DeviceID, r.Name, r.Condition, r.Action, r.Enabled)

	var out Rule
	if err := row.Scan(&out.ID, &out.ZoneID, &out.DeviceID, &out.Name, &out.Condition, &out.Action, &out.Enabled); err != nil {
		return nil, err
	}
	return &out, nil
}

func (db *DB) SetRuleEnabled(ctx context.Context, id string, enabled bool) error {
	_, err := db.Pool.Exec(ctx, `update automation_rules set enabled = $2 where id = $1`, id, enabled)
	return err
}

func (db *DB) DeleteRule(ctx context.Context, id string) error {
	_, err := db.Pool.Exec(ctx, `delete from automation_rules where id = $1`, id)
	return err
}

func (db *DB) CreateAlert(ctx context.Context, deviceID string, ruleID *string, severity, message string) error {
	_, err := db.Pool.Exec(ctx, `
		insert into alerts (id, device_id, rule_id, severity, message)
		values ($1, $2, $3, $4, $5)
	`, uuid.NewString(), deviceID, ruleID, severity, message)
	return err
}
