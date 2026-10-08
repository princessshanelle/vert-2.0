package api

import (
	"context"
	"net/http"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

// Claims mirrors services/gateway/internal/api/middleware.go's Claims
// exactly: both services sign and verify with the same VERT_JWT_SECRET,
// so a token from the gateway's /auth/login is also valid here. This is
// what lets one login cover both services without a shared session store.
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

			claims := &Claims{}
			token, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (interface{}, error) {
				return []byte(secret), nil
			})
			if err != nil || !token.Valid {
				writeError(w, http.StatusUnauthorized, "invalid or expired token")
				return
			}

			ctx := context.WithValue(r.Context(), claimsKey, claims)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// RequireAnyRole gates rule management to the people actually running the
// greenhouse day to day. A viewer account -- e.g. a family member who
// just wants to check in on the plants -- can look, but automation that
// controls pumps and vents is left to admins and operators.
func RequireAnyRole(roles ...string) func(http.Handler) http.Handler {
	allowed := make(map[string]bool, len(roles))
	for _, r := range roles {
		allowed[r] = true
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			claims, _ := r.Context().Value(claimsKey).(*Claims)
			if claims == nil || !allowed[claims.Role] {
				writeError(w, http.StatusForbidden, "your account doesn't have permission to manage automation rules")
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
