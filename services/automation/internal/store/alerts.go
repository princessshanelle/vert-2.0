package store

import (
	"context"
	"time"
)

type Alert struct {
	ID           string    `json:"id"`
	DeviceID     string    `json:"device_id"`
	RuleID       *string   `json:"rule_id,omitempty"`
	Severity     string    `json:"severity"`
	Message      string    `json:"message"`
	Acknowledged bool      `json:"acknowledged"`
	CreatedAt    time.Time `json:"created_at"`
}

// ListAlerts returns the most recent alerts first, unacknowledged ones
// naturally floating to what a person actually needs to look at -- the
// frontend doesn't need to do any of that sorting itself.
func (db *DB) ListAlerts(ctx context.Context, limit int) ([]Alert, error) {
	rows, err := db.Pool.Query(ctx, `
		select id, device_id, rule_id, severity, message, acknowledged, created_at
		from alerts
		order by acknowledged asc, created_at desc
		limit $1
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Alert
	for rows.Next() {
		var a Alert
		if err := rows.Scan(&a.ID, &a.DeviceID, &a.RuleID, &a.Severity, &a.Message, &a.Acknowledged, &a.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

func (db *DB) AcknowledgeAlert(ctx context.Context, id string) error {
	_, err := db.Pool.Exec(ctx, `update alerts set acknowledged = true where id = $1`, id)
	return err
}
