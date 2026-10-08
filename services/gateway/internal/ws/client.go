package ws

import (
	"net/http"
	"time"

	"github.com/gorilla/websocket"
)

const (
	writeWait  = 10 * time.Second
	pongWait   = 60 * time.Second
	pingPeriod = (pongWait * 9) / 10
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	// The gateway already serves the API with Access-Control-Allow-Origin: *
	// (see corsMiddleware) because the PWA and API can sit on different
	// LAN ports/hosts; a WebSocket has no equivalent CORS check at all,
	// only Origin, so this matches that existing same-network trust
	// boundary rather than introducing a stricter rule only here.
	CheckOrigin: func(r *http.Request) bool { return true },
}

// Serve upgrades an already-authenticated request to a WebSocket and
// streams every subsequent broadcast to it until the connection drops.
// Authentication happens in the caller (api.WSHandler) before this is
// invoked, because it needs the JWT-parsing helper that already lives in
// package api, and importing api from here would be a cycle.
func (h *Hub) Serve(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		// Upgrade already wrote an HTTP error response on failure;
		// nothing more to do here.
		return
	}

	c := &client{send: make(chan []byte, outboxSize)}
	h.register <- c

	go h.writePump(conn, c)
	h.readPump(conn, c)
}

// readPump's only real job is noticing the connection is gone: dashboard
// clients don't send us anything. Reading is still required so the
// gorilla/websocket library processes control frames (pings/pongs/close)
// and so a dead TCP connection is detected instead of leaking forever.
func (h *Hub) readPump(conn *websocket.Conn, c *client) {
	defer func() {
		h.unregister <- c
		conn.Close()
	}()
	conn.SetReadLimit(512)
	_ = conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(pongWait))
	})
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
	}
}

// writePump owns the connection's writes exclusively (gorilla/websocket
// forbids concurrent writers), draining the client's outbox and sending
// periodic pings so a half-open connection -- the network dropped but no
// FIN/RST ever arrived -- gets noticed within pongWait instead of holding
// a phantom "connected" client in the hub forever.
func (h *Hub) writePump(conn *websocket.Conn, c *client) {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		conn.Close()
	}()
	for {
		select {
		case payload, ok := <-c.send:
			_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				_ = conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := conn.WriteMessage(websocket.TextMessage, payload); err != nil {
				return
			}
		case <-ticker.C:
			_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}
