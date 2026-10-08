package store

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
)

func hashSecret(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:])
}

// IssueDeviceCredential records a new (or replaced) secret for a device.
// Re-provisioning a device is intentional and safe: the old secret simply
// stops working the moment the new hash is written, which is what you
// want if a device's credentials are ever suspected lost or copied.
func (db *DB) IssueDeviceCredential(ctx context.Context, deviceID, secret, issuedByUserID string) error {
	_, err := db.Pool.Exec(ctx, `
		insert into device_credentials (device_id, secret_hash, issued_by)
		values ($1, $2, $3)
		on conflict (device_id) do update
		set secret_hash = excluded.secret_hash,
		    issued_by = excluded.issued_by,
		    issued_at = now(),
		    revoked_at = null
	`, deviceID, hashSecret(secret), issuedByUserID)
	return err
}

// VerifyDeviceCredential is on the hot path for every device connection
// (MQTT) and every frame upload (HTTP), so it does one indexed lookup and
// a constant-time comparison -- no bcrypt here, see the schema comment in
// infra/postgres/init.sql for why that's the right call for machine
// credentials.
func (db *DB) VerifyDeviceCredential(ctx context.Context, deviceID, secret string) (bool, error) {
	var hash string
	var revoked bool
	row := db.Pool.QueryRow(ctx, `
		select secret_hash, revoked_at is not null
		from device_credentials where device_id = $1
	`, deviceID)
	if err := row.Scan(&hash, &revoked); err != nil {
		return false, nil // unknown device: not an error, just "no"
	}
	if revoked {
		return false, nil
	}
	want := hashSecret(secret)
	return subtle.ConstantTimeCompare([]byte(hash), []byte(want)) == 1, nil
}

func (db *DB) RevokeDeviceCredential(ctx context.Context, deviceID string) error {
	_, err := db.Pool.Exec(ctx, `
		update device_credentials set revoked_at = now() where device_id = $1
	`, deviceID)
	return err
}
