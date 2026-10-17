package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

var ErrNotFound = errors.New("not found")
var ErrAlreadyExists = errors.New("already exists")

type User struct {
	ID           uuid.UUID `json:"id"`
	Email        string    `json:"email"`
	PasswordHash string    `json:"-"` // never serialized -- see AuthHandler.Me
	DisplayName  string    `json:"display_name"`
	Locale       string    `json:"locale"`
	Role         string    `json:"role"`
	// TokenVersion is stamped into every session token ("tv"); bumping it
	// (password change or reset, removing access) ends all existing sessions.
	TokenVersion int       `json:"-"`
	Disabled     bool      `json:"disabled"`
	CreatedAt    time.Time `json:"created_at"`
}

const userColumns = `id, email, password_hash, display_name, locale, role, token_version, disabled, created_at`

func scanUser(row interface{ Scan(...any) error }) (*User, error) {
	var u User
	if err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.DisplayName, &u.Locale, &u.Role,
		&u.TokenVersion, &u.Disabled, &u.CreatedAt); err != nil {
		return nil, err
	}
	return &u, nil
}

// EnsureSchema brings an existing database up to what this gateway needs.
// infra/postgres/init.sql only runs when the volume is first created, so a
// greenhouse that's been running for months gets new columns here instead.
// Every statement is idempotent; it runs on each start.
func (db *DB) EnsureSchema(ctx context.Context) error {
	_, err := db.Pool.Exec(ctx, `
		alter table users add column if not exists token_version int not null default 0;
		alter table users add column if not exists disabled boolean not null default false;
	`)
	return err
}

// CreateUser makes the very first account on a fresh VERT instance an
// admin automatically. Nobody setting up a greenhouse for the first time
// should have to know about a hidden bootstrap step or a default
// superuser password just to get past the front door -- the first
// person in is trusted by definition, because they're the one who stood
// the server up.
func (db *DB) CreateUser(ctx context.Context, email, passwordHash, displayName, locale string) (*User, error) {
	row := db.Pool.QueryRow(ctx, `
		insert into users (email, password_hash, display_name, locale, role)
		values ($1, $2, $3, $4,
			case when exists (select 1 from users) then 'operator' else 'admin' end)
		returning `+userColumns+`
	`, email, passwordHash, displayName, locale)
	// A unique violation on email -> already exists. Callers can inspect the
	// underlying pgx error code if they need to distinguish further.
	return scanUser(row)
}

// CreateUserWithRole is an admin adding someone: the role is chosen, not
// inferred from whether this is the first account.
func (db *DB) CreateUserWithRole(ctx context.Context, email, passwordHash, displayName, role string) (*User, error) {
	row := db.Pool.QueryRow(ctx, `
		insert into users (email, password_hash, display_name, locale, role)
		values ($1, $2, $3, 'en', $4)
		returning `+userColumns, email, passwordHash, displayName, role)
	return scanUser(row)
}

func (db *DB) GetUserByEmail(ctx context.Context, email string) (*User, error) {
	u, err := scanUser(db.Pool.QueryRow(ctx, `select `+userColumns+` from users where email = $1`, email))
	if err != nil {
		return nil, ErrNotFound
	}
	return u, nil
}

func (db *DB) GetUserByID(ctx context.Context, id string) (*User, error) {
	u, err := scanUser(db.Pool.QueryRow(ctx, `select `+userColumns+` from users where id::text = $1`, id))
	if err != nil {
		return nil, ErrNotFound
	}
	return u, nil
}

func (db *DB) ListUsers(ctx context.Context) ([]User, error) {
	rows, err := db.Pool.Query(ctx, `select `+userColumns+` from users order by created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []User{}
	for rows.Next() {
		u, err := scanUser(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *u)
	}
	return out, rows.Err()
}

// SetPassword replaces the hash and ends every session of that user.
func (db *DB) SetPassword(ctx context.Context, id, passwordHash string) (*User, error) {
	u, err := scanUser(db.Pool.QueryRow(ctx, `
		update users set password_hash = $2, token_version = token_version + 1
		where id::text = $1 returning `+userColumns, id, passwordHash))
	if err != nil {
		return nil, ErrNotFound
	}
	return u, nil
}

// SetEmail fails with a unique violation if the address is taken.
func (db *DB) SetEmail(ctx context.Context, id, email string) (*User, error) {
	return scanUser(db.Pool.QueryRow(ctx, `
		update users set email = $2 where id::text = $1 returning `+userColumns, id, email))
}

func (db *DB) SetRole(ctx context.Context, id, role string) (*User, error) {
	u, err := scanUser(db.Pool.QueryRow(ctx, `
		update users set role = $2 where id::text = $1 returning `+userColumns, id, role))
	if err != nil {
		return nil, ErrNotFound
	}
	return u, nil
}

// SetDisabled removes (or restores) access; removing also ends every session.
func (db *DB) SetDisabled(ctx context.Context, id string, disabled bool) (*User, error) {
	u, err := scanUser(db.Pool.QueryRow(ctx, `
		update users set disabled = $2,
		       token_version = token_version + case when $2 then 1 else 0 end
		where id::text = $1 returning `+userColumns, id, disabled))
	if err != nil {
		return nil, ErrNotFound
	}
	return u, nil
}

// CountActiveAdmins guards against locking everyone out of user management.
func (db *DB) CountActiveAdmins(ctx context.Context) (int, error) {
	var n int
	err := db.Pool.QueryRow(ctx, `select count(*) from users where role = 'admin' and not disabled`).Scan(&n)
	return n, err
}
