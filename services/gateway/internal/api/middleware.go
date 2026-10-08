package api

import (
	"context"
	"net/http"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

type Claims struct {
	UserID string `json:"uid"`
	Email  string `json:"email"`
	Role   string `json:"role"`
	jwt.RegisteredClaims
}

type contextKey string

const claimsKey contextKey = "claims"

func RequireAuth(secret string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			header := r.Header.Get("Authorization")
			if !strings.HasPrefix(header, "Bearer ") {
				writeError(w, http.StatusUnauthorized, "missing bearer token")
				return
			}
			raw := strings.TrimPrefix(header, "Bearer ")

			claims, err := parseToken(secret, raw)
			if err != nil {
				writeError(w, http.StatusUnauthorized, "invalid or expired token")
				return
			}

			ctx := context.WithValue(r.Context(), claimsKey, claims)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// parseToken is the one place a signed JWT becomes trusted Claims, shared
// by RequireAuth (token in the Authorization header) and the WebSocket
// handler (token in a query parameter, since browsers can't set headers
// on a WS handshake) so the two paths can never drift in how they verify
// a token.
func parseToken(secret, raw string) (*Claims, error) {
	claims := &Claims{}
	token, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (interface{}, error) {
		return []byte(secret), nil
	})
	if err != nil {
		return nil, err
	}
	if !token.Valid {
		return nil, jwt.ErrTokenSignatureInvalid
	}
	return claims, nil
}

func ClaimsFromContext(ctx context.Context) *Claims {
	claims, _ := ctx.Value(claimsKey).(*Claims)
	return claims
}

// RequireRole gates an action to a specific account role (e.g. "admin").
// Provisioning a device hands out a credential that controls a real,
// physical thing in someone's greenhouse -- a pump, a window motor -- so
// that action is deliberately not available to every logged-in viewer.
func RequireRole(role string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims := ClaimsFromContext(r.Context())
			if claims == nil || claims.Role != role {
				writeError(w, http.StatusForbidden, "this action needs an "+role+" account")
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// RequireAnyRole gates an action to a set of roles -- manual device
// control needs someone actually running the greenhouse day to day
// (admin or operator), but not necessarily a full admin.
func RequireAnyRole(roles ...string) func(http.Handler) http.Handler {
	allowed := make(map[string]bool, len(roles))
	for _, r := range roles {
		allowed[r] = true
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims := ClaimsFromContext(r.Context())
			if claims == nil || !allowed[claims.Role] {
				writeError(w, http.StatusForbidden, "your account doesn't have permission to do that")
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
