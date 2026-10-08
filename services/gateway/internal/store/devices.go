package store

import (
	"context"
	"encoding/json"
	"time"
)

type Device struct {
	ID           string          `json:"id"`
	DeviceType   string          `json:"device_type"`
	ZoneID       *string         `json:"zone_id"`
	Capabilities json.RawMessage `json:"capabilities"`
	DisplayName  string          `json:"display_name"`
	Status       string          `json:"status"`
	LastSeenAt   *time.Time      `json:"last_seen_at"`
	LastReading  json.RawMessage `json:"last_reading"`
	LocalIP      *string         `json:"local_ip"`
}

// UpsertDevice records (or refreshes) a device's registration, including
// the LAN address it announced itself from. Called both when a device
// announces itself for the first time and on every subsequent boot, so
// registration is idempotent by design. localIP is optional -- older
// firmware that doesn't report one just leaves the column as it was.
func (db *DB) UpsertDevice(ctx context.Context, id, deviceType string, capabilities json.RawMessage, localIP string) error {
	var ipArg any
	if localIP != "" {
		ipArg = localIP
	}
	_, err := db.Pool.Exec(ctx, `
		insert into devices (id, device_type, capabilities, status, last_seen_at, local_ip)
		values ($1, $2, $3, 'online', now(), $4)
		on conflict (id) do update
		set device_type = excluded.device_type,
		    capabilities = excluded.capabilities,
		    status = 'online',
		    last_seen_at = now(),
		    local_ip = coalesce(excluded.local_ip, devices.local_ip)
	`, id, deviceType, capabilities, ipArg)
	return err
}

// EnsureDevice creates a device row if one doesn't exist yet, so a device
// can be provisioned with credentials (see IssueDeviceCredential) before
// it has ever powered on and announced its capabilities. Capabilities
// start empty and are filled in the moment the real device first connects.
func (db *DB) EnsureDevice(ctx context.Context, id, deviceType string) error {
	_, err := db.Pool.Exec(ctx, `
		insert into devices (id, device_type, capabilities, status)
		values ($1, $2, '{}'::jsonb, 'offline')
		on conflict (id) do nothing
	`, id, deviceType)
	return err
}

func (db *DB) MarkDeviceStatus(ctx context.Context, id, status string) error {
	_, err := db.Pool.Exec(ctx, `
		update devices set status = $2, last_seen_at = now() where id = $1
	`, id, status)
	return err
}

// UpdateDeviceReading remembers the most recent payload a device (or an
// AI model publishing under that device's id, see services/inference)
// reported, so the dashboard has something real to show -- a current
// temperature, a plant-health note -- rather than just an online dot.
// This is deliberately "latest value only": full time-series history now
// lives in TimescaleDB (see internal/timeseries) for charts; this column
// is just what the device cards render without a second request.
func (db *DB) UpdateDeviceReading(ctx context.Context, id string, payload json.RawMessage) error {
	_, err := db.Pool.Exec(ctx, `
		update devices set last_reading = $2, status = 'online', last_seen_at = now() where id = $1
	`, id, payload)
	return err
}

func (db *DB) ListDevices(ctx context.Context) ([]Device, error) {
	rows, err := db.Pool.Query(ctx, `
		select id, device_type, zone_id, capabilities, coalesce(display_name, id), status, last_seen_at, last_reading, local_ip
		from devices order by created_at
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Device
	for rows.Next() {
		var d Device
		if err := rows.Scan(&d.ID, &d.DeviceType, &d.ZoneID, &d.Capabilities, &d.DisplayName, &d.Status, &d.LastSeenAt, &d.LastReading, &d.LocalIP); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

func (db *DB) GetDevice(ctx context.Context, id string) (*Device, error) {
	row := db.Pool.QueryRow(ctx, `
		select id, device_type, zone_id, capabilities, coalesce(display_name, id), status, last_seen_at, last_reading, local_ip
		from devices where id = $1
	`, id)

	var d Device
	if err := row.Scan(&d.ID, &d.DeviceType, &d.ZoneID, &d.Capabilities, &d.DisplayName, &d.Status, &d.LastSeenAt, &d.LastReading, &d.LocalIP); err != nil {
		return nil, ErrNotFound
	}
	return &d, nil
}
