package api

import (
	"context"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/gateway/internal/store"
)

type deviceContextKey string

const deviceIDKey deviceContextKey = "device_id"

// RequireDeviceAuth checks the HTTP Basic credentials a device sends
// against the secret issued to it at provisioning time (see
// devices_provision.go). It exists so that a frame upload is treated
// with the same care as anything else that writes to the system: nobody
// should be able to push images into another grower's dashboard just by
// guessing a device id.
func RequireDeviceAuth(db *store.DB) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			deviceID := chi.URLParam(r, "deviceID")
			username, password, ok := r.BasicAuth()
			if !ok || username == "" || username != deviceID {
				writeError(w, http.StatusUnauthorized, "device credentials required")
				return
			}

			valid, err := db.VerifyDeviceCredential(r.Context(), deviceID, password)
			if err != nil {
				writeError(w, http.StatusInternalServerError, "could not verify device credentials")
				return
			}
			if !valid {
				writeError(w, http.StatusUnauthorized, "invalid device credentials")
				return
			}

			ctx := context.WithValue(r.Context(), deviceIDKey, deviceID)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
