package store

import (
	"context"
	"time"
)

type Reminder struct {
	ID          string    `json:"id"`
	UserID      string    `json:"user_id"`
	Title       string    `json:"title"`
	Description string    `json:"description"`
	DueAt       time.Time `json:"due_at"`
	Recurrence  *string   `json:"recurrence,omitempty"`
	Completed   bool      `json:"completed"`
	CreatedAt   time.Time `json:"created_at"`
}

// Reminders are deliberately per-user, not shared across the whole
// greenhouse team: "remember to reorder pH strips" is something one
// person set for themselves, not a broadcast to everyone with a login.
func (db *DB) CreateReminder(ctx context.Context, userID, title, description string, dueAt time.Time, recurrence *string) (*Reminder, error) {
	row := db.Pool.QueryRow(ctx, `
		insert into reminders (user_id, title, description, due_at, recurrence)
		values ($1, $2, $3, $4, $5)
		returning id, user_id, title, coalesce(description, ''), due_at, recurrence, completed, created_at
	`, userID, title, description, dueAt, recurrence)

	var rem Reminder
	if err := row.Scan(&rem.ID, &rem.UserID, &rem.Title, &rem.Description, &rem.DueAt, &rem.Recurrence, &rem.Completed, &rem.CreatedAt); err != nil {
		return nil, err
	}
	return &rem, nil
}

func (db *DB) ListReminders(ctx context.Context, userID string) ([]Reminder, error) {
	rows, err := db.Pool.Query(ctx, `
		select id, user_id, title, coalesce(description, ''), due_at, recurrence, completed, created_at
		from reminders
		where user_id = $1
		order by completed asc, due_at asc
	`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Reminder
	for rows.Next() {
		var rem Reminder
		if err := rows.Scan(&rem.ID, &rem.UserID, &rem.Title, &rem.Description, &rem.DueAt, &rem.Recurrence, &rem.Completed, &rem.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, rem)
	}
	return out, rows.Err()
}

// SetReminderCompleted only touches the row if it belongs to the
// requesting user -- one person's reminder is never another's to check
// off, even by accident.
func (db *DB) SetReminderCompleted(ctx context.Context, id, userID string, completed bool) error {
	_, err := db.Pool.Exec(ctx, `
		update reminders set completed = $3 where id = $1 and user_id = $2
	`, id, userID, completed)
	return err
}

func (db *DB) DeleteReminder(ctx context.Context, id, userID string) error {
	_, err := db.Pool.Exec(ctx, `delete from reminders where id = $1 and user_id = $2`, id, userID)
	return err
}
