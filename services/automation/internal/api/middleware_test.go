package api

import (
	"context"
	"testing"

	"github.com/vert-project/automation/internal/store"
)

type fakeUsers map[string]*store.SessionUser

func (f fakeUsers) SessionUser(_ context.Context, id string) (*store.SessionUser, error) {
	if u, ok := f[id]; ok {
		return u, nil
	}
	return nil, store.ErrNotFound
}

func TestCheckSession(t *testing.T) {
	users := fakeUsers{"u1": {Email: "a@example.com", Role: "viewer", TokenVersion: 1}}
	c := &Claims{UserID: "u1", Role: "admin", TokenVersion: 1}
	if err := checkSession(context.Background(), users, c); err != nil || c.Role != "viewer" {
		t.Fatalf("current session: err %v, role %q (want viewer from the database)", err, c.Role)
	}
	if checkSession(context.Background(), users, &Claims{UserID: "u1", TokenVersion: 0}) == nil {
		t.Error("an ended session was accepted")
	}
	if checkSession(context.Background(), users, &Claims{UserID: "nobody"}) == nil {
		t.Error("an unknown account was accepted")
	}
	users["u1"].Disabled = true
	if checkSession(context.Background(), users, &Claims{UserID: "u1", TokenVersion: 1}) == nil {
		t.Error("a disabled account was accepted")
	}
}
