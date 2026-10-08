package store

import (
	"context"
	"time"
)

type Frame struct {
	ID          string
	DeviceID    string
	ObjectKey   string
	ContentType string
	SizeBytes   int64
	CreatedAt   time.Time
}

func (db *DB) CreateFrame(ctx context.Context, deviceID, objectKey, contentType string, sizeBytes int64) error {
	_, err := db.Pool.Exec(ctx, `
		insert into frames (device_id, object_key, content_type, size_bytes)
		values ($1, $2, $3, $4)
	`, deviceID, objectKey, contentType, sizeBytes)
	return err
}

func (db *DB) GetFrameObjectKey(ctx context.Context, frameID string) (string, error) {
	var key string
	row := db.Pool.QueryRow(ctx, `select object_key from frames where id = $1`, frameID)
	if err := row.Scan(&key); err != nil {
		return "", ErrNotFound
	}
	return key, nil
}

func (db *DB) ListFramesByDevice(ctx context.Context, deviceID string, limit int) ([]Frame, error) {
	rows, err := db.Pool.Query(ctx, `
		select id, device_id, object_key, content_type, size_bytes, created_at
		from frames where device_id = $1
		order by created_at desc
		limit $2
	`, deviceID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Frame
	for rows.Next() {
		var f Frame
		if err := rows.Scan(&f.ID, &f.DeviceID, &f.ObjectKey, &f.ContentType, &f.SizeBytes, &f.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}
