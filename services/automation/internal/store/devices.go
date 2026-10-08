package store

import "context"

// DeviceInfo is the subset of a device's registration automation needs
// to (a) decide which rules apply to it and (b) address a command back
// to some other device. ZoneSlug is the zones.name value, which must
// match the ZONE a device's firmware was flashed with -- see
// docs/TESTING.md for why that convention matters here.
type DeviceInfo struct {
	DeviceType string
	ZoneID     *string
	ZoneSlug   string
}

func (db *DB) GetDeviceInfo(ctx context.Context, deviceID string) (*DeviceInfo, error) {
	row := db.Pool.QueryRow(ctx, `
		select d.device_type, d.zone_id, coalesce(z.name, '')
		from devices d
		left join zones z on z.id = d.zone_id
		where d.id = $1
	`, deviceID)

	var info DeviceInfo
	if err := row.Scan(&info.DeviceType, &info.ZoneID, &info.ZoneSlug); err != nil {
		return nil, ErrNotFound
	}
	return &info, nil
}
