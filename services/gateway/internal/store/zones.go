package store

import "context"

type Zone struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

func (db *DB) CreateZone(ctx context.Context, name string) (*Zone, error) {
	row := db.Pool.QueryRow(ctx, `
		insert into zones (name) values ($1)
		returning id, name
	`, name)
	var z Zone
	if err := row.Scan(&z.ID, &z.Name); err != nil {
		return nil, err
	}
	return &z, nil
}

func (db *DB) ListZones(ctx context.Context) ([]Zone, error) {
	rows, err := db.Pool.Query(ctx, `select id, name from zones order by name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Zone
	for rows.Next() {
		var z Zone
		if err := rows.Scan(&z.ID, &z.Name); err != nil {
			return nil, err
		}
		out = append(out, z)
	}
	return out, rows.Err()
}

// ZoneName resolves an id to the slug used in routing keys/topics --
// devices know their zone by that name (the ZONE macro in firmware),
// never by the database's UUID for it.
func (db *DB) ZoneName(ctx context.Context, zoneID string) (string, error) {
	var name string
	row := db.Pool.QueryRow(ctx, `select name from zones where id = $1`, zoneID)
	if err := row.Scan(&name); err != nil {
		return "", ErrNotFound
	}
	return name, nil
}

func (db *DB) AssignDeviceZone(ctx context.Context, deviceID, zoneID string) error {
	_, err := db.Pool.Exec(ctx, `update devices set zone_id = $2 where id = $1`, deviceID, zoneID)
	return err
}
