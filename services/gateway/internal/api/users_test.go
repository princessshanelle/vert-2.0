package api

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"

	"github.com/vert-project/gateway/internal/store"
)

func ptr[T any](v T) *T { return &v }

func TestCheckAccountChange(t *testing.T) {
	me := uuid.New()
	admin := &store.User{ID: uuid.New(), Role: "admin"}
	operator := &store.User{ID: uuid.New(), Role: "operator"}
	disabledAdmin := &store.User{ID: uuid.New(), Role: "admin", Disabled: true}
	self := &store.User{ID: me, Role: "admin"}

	cases := []struct {
		name    string
		target  *store.User
		role    *string
		disable *bool
		admins  int
		want    error
	}{
		{"promote operator", operator, ptr("admin"), nil, 1, nil},
		{"demote one of two admins", admin, ptr("operator"), nil, 2, nil},
		{"demote the last admin", admin, ptr("viewer"), nil, 1, errLastAdmin},
		{"disable the last admin", admin, nil, ptr(true), 1, errLastAdmin},
		{"re-save the last admin as admin", admin, ptr("admin"), nil, 1, nil},
		{"restore a disabled admin", disabledAdmin, nil, ptr(false), 1, nil},
		{"change own role", self, ptr("operator"), nil, 3, errSelfChange},
		{"disable self", self, nil, ptr(true), 3, errSelfChange},
		{"unknown role", operator, ptr("superuser"), nil, 2, errBadRole},
		{"disable an operator", operator, nil, ptr(true), 1, nil},
	}
	for _, c := range cases {
		if got := checkAccountChange(me.String(), c.target, c.role, c.disable, c.admins); !errors.Is(got, c.want) {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}

func TestValidEmailAndPassword(t *testing.T) {
	for _, s := range []string{"you@example.com", "a.b@greenhouse.ng"} {
		if !validEmail(s) {
			t.Errorf("validEmail(%q) = false", s)
		}
	}
	for _, s := range []string{"", "you", "you@", "@example.com", "you@example", "a@b@c.com", "you @example.com"} {
		if validEmail(s) {
			t.Errorf("validEmail(%q) = true", s)
		}
	}
	if validPassword("1234567") || !validPassword("12345678") {
		t.Error("validPassword: 8 characters is the minimum")
	}
	if normaliseEmail("  You@Example.COM ") != "you@example.com" {
		t.Error("normaliseEmail should trim and lowercase")
	}
}

type fakeUsers map[string]*store.User

func (f fakeUsers) GetUserByID(_ context.Context, id string) (*store.User, error) {
	if u, ok := f[id]; ok {
		return u, nil
	}
	return nil, store.ErrNotFound
}

func TestCheckSession(t *testing.T) {
	id := uuid.New()
	users := fakeUsers{id.String(): {ID: id, Email: "new@example.com", Role: "viewer", TokenVersion: 2}}

	ok := &Claims{UserID: id.String(), Email: "old@example.com", Role: "admin", TokenVersion: 2}
	if err := checkSession(context.Background(), users, ok); err != nil {
		t.Fatalf("current session rejected: %v", err)
	}
	if ok.Role != "viewer" || ok.Email != "new@example.com" {
		t.Errorf("claims not refreshed from the database: %+v", ok)
	}
	if err := checkSession(context.Background(), users, &Claims{UserID: id.String(), TokenVersion: 1}); err == nil {
		t.Error("a session from before a password change was accepted")
	}
	if err := checkSession(context.Background(), users, &Claims{UserID: uuid.NewString()}); err == nil {
		t.Error("a session for an unknown account was accepted")
	}
	users[id.String()].Disabled = true
	if err := checkSession(context.Background(), users, &Claims{UserID: id.String(), TokenVersion: 2}); err == nil {
		t.Error("a session of a disabled account was accepted")
	}
}
