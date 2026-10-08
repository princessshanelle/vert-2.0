// Package timeseries is the gateway's connection to TimescaleDB: the real
// history behind every chart on the dashboard. It is deliberately a
// separate, narrow package from internal/store (Postgres) -- a reading
// history and an account/device registry have completely different
// access patterns and lifetimes, and keeping them apart means a bug or
// a slow query in one can never be mistaken for, or block, the other.
package timeseries

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type DB struct {
	Pool *pgxpool.Pool
}

func Connect(ctx context.Context, url string) (*DB, error) {
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		return nil, err
	}
	return &DB{Pool: pool}, nil
}

func (db *DB) Close() {
	db.Pool.Close()
}

// InsertReading records every numeric field of a telemetry payload as
// its own row. Non-numeric fields (an AI result's label/note strings)
// are silently skipped -- they aren't chartable, and the gateway's
// devices.last_reading column already keeps the full payload for
// display. One bad or empty payload should never be a reason to drop
// the numeric fields that ARE readable, so each metric is inserted
// independently within the batch rather than all-or-nothing.
func (db *DB) InsertReading(ctx context.Context, deviceID string, ts time.Time, payload map[string]any) error {
	var batch pgx.Batch
	for metric, raw := range payload {
		value, ok := toFloat(raw)
		if !ok {
			continue
		}
		batch.Queue(`insert into readings (device_id, ts, metric, value) values ($1, $2, $3, $4)`,
			deviceID, ts, metric, value)
	}
	if batch.Len() == 0 {
		return nil
	}
	results := db.Pool.SendBatch(ctx, &batch)
	defer results.Close()
	for i := 0; i < batch.Len(); i++ {
		if _, err := results.Exec(); err != nil {
			return err
		}
	}
	return nil
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	}
	return 0, false
}

// Point is one plotted value.
type Point struct {
	Time  time.Time `json:"t"`
	Value float64   `json:"v"`
}

// Metrics lists which numeric fields a device has ever reported, newest
// first by nothing in particular -- just distinct names -- so the
// frontend knows what chart tabs to offer without the caller having to
// already know a device's shape in advance.
func (db *DB) Metrics(ctx context.Context, deviceID string) ([]string, error) {
	rows, err := db.Pool.Query(ctx, `
		select distinct metric from readings where device_id = $1 order by metric
	`, deviceID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []string
	for rows.Next() {
		var m string
		if err := rows.Scan(&m); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// History returns points for one device+metric within the last `since`
// duration, oldest first (the order a chart wants to draw them in).
func (db *DB) History(ctx context.Context, deviceID, metric string, since time.Duration) ([]Point, error) {
	rows, err := db.Pool.Query(ctx, `
		select ts, value from readings
		where device_id = $1 and metric = $2 and ts > now() - $3::interval
		order by ts asc
	`, deviceID, metric, since.String())
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Point
	for rows.Next() {
		var p Point
		if err := rows.Scan(&p.Time, &p.Value); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
