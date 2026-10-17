package store

import "context"

// SessionUser is what the API needs to trust a session token: the gateway
// issues the tokens, but both services check them against the same users
// table so a password change or removed access ends the session here too.
type SessionUser struct {
	Email        string
	Role         string
	TokenVersion int
	Disabled     bool
}

func (db *DB) SessionUser(ctx context.Context, id string) (*SessionUser, error) {
	var u SessionUser
	err := db.Pool.QueryRow(ctx, `
		select email, role, token_version, disabled from users where id::text = $1
	`, id).Scan(&u.Email, &u.Role, &u.TokenVersion, &u.Disabled)
	if err != nil {
		return nil, ErrNotFound
	}
	return &u, nil
}

// EnsureSchema adds the session columns if this service starts before the
// gateway has (the gateway runs the same idempotent statements).
func (db *DB) EnsureSchema(ctx context.Context) error {
	_, err := db.Pool.Exec(ctx, `
		alter table users add column if not exists token_version int not null default 0;
		alter table users add column if not exists disabled boolean not null default false;
	`)
	return err
}
