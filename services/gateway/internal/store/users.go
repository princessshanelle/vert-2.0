package store

import (
	"context"
	"errors"

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
		returning id, email, password_hash, display_name, locale, role
	`, email, passwordHash, displayName, locale)

	var u User
	if err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.DisplayName, &u.Locale, &u.Role); err != nil {
		// Unique violation on email -> already exists. Callers can inspect
		// the underlying pgx error code if they need to distinguish further.
		return nil, err
	}
	return &u, nil
}

func (db *DB) GetUserByEmail(ctx context.Context, email string) (*User, error) {
	row := db.Pool.QueryRow(ctx, `
		select id, email, password_hash, display_name, locale, role
		from users where email = $1
	`, email)

	var u User
	if err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.DisplayName, &u.Locale, &u.Role); err != nil {
		return nil, ErrNotFound
	}
	return &u, nil
}
