package api

import (
	"net/http"

	"github.com/vert-project/gateway/internal/ws"
)

type WSHandler struct {
	Hub       *ws.Hub
	JWTSecret string
	Users     SessionUsers
}

// Readings upgrades to a WebSocket that streams every telemetry update
// live. It can't reuse the RequireAuth middleware: browsers' WebSocket
// API has no way to set an Authorization header on the handshake, so the
// token travels as a query parameter instead -- the one part of this
// request a user's own browser controls, unlike a header a proxy or log
// line might otherwise capture it from.
func (h *WSHandler) Readings(w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		writeError(w, http.StatusUnauthorized, "missing token")
		return
	}
	claims, err := parseToken(h.JWTSecret, token)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "invalid or expired token")
		return
	}
	if err := checkSession(r.Context(), h.Users, claims); err != nil {
		writeError(w, http.StatusUnauthorized, "your session has ended -- please log in again")
		return
	}
	h.Hub.Serve(w, r)
}
