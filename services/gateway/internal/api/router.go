package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/vert-project/gateway/internal/config"
	"github.com/vert-project/gateway/internal/mq"
	"github.com/vert-project/gateway/internal/storage"
	"github.com/vert-project/gateway/internal/store"
	"github.com/vert-project/gateway/internal/timeseries"
	"github.com/vert-project/gateway/internal/ws"
)

// NewRouter builds the HTTP API. It deliberately takes no AMQP connection:
// the gateway's relationship with RabbitMQ (connect, reconnect, consume)
// is owned entirely by runMessaging in cmd/gateway/main.go, and nothing
// in this package ever needs to reach into a connection that might have
// been silently replaced after a reconnect. Commander is the one
// exception -- it's built specifically to stay valid across reconnects
// (see internal/mq/commander.go), which is what lets a handler use it
// safely.
func NewRouter(db *store.DB, tsdb *timeseries.DB, objects *storage.ObjectStore, commander *mq.Commander, hub *ws.Hub, cfg config.Config) http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(corsMiddleware)

	authH := &AuthHandler{DB: db, JWTSecret: cfg.JWTSecret}
	deviceH := &DeviceHandler{DB: db}
	provisionH := &DeviceProvisionHandler{DB: db}
	frameH := &FrameHandler{DB: db, Objects: objects}
	zoneH := &ZoneHandler{DB: db}
	remindersH := &RemindersHandler{DB: db}
	readingsH := &ReadingsHandler{TS: tsdb}
	commandH := &CommandHandler{DB: db, Commander: commander}
	rmqAuthH := &RabbitMQAuthHandler{DB: db}
	wsH := &WSHandler{Hub: hub, JWTSecret: cfg.JWTSecret}

	r.Route("/api/v1", func(r chi.Router) {
		r.Post("/auth/register", authH.Register)
		r.Post("/auth/login", authH.Login)

		// Not behind RequireAuth: a WebSocket handshake from a browser
		// can't carry an Authorization header, so WSHandler.Readings
		// checks the same JWT itself, passed as ?token= instead.
		r.Get("/ws/readings", wsH.Readings)

		// Device-to-gateway upload, authenticated by the per-device
		// credential issued at provisioning time -- never a user JWT,
		// since the device on the bench has no login of its own.
		r.Group(func(r chi.Router) {
			r.Use(RequireDeviceAuth(db))
			r.Post("/devices/{deviceID}/frames", frameH.Upload)
		})

		r.Group(func(r chi.Router) {
			r.Use(RequireAuth(cfg.JWTSecret))
			r.Get("/me", authH.Me)
			r.Get("/devices", deviceH.List)
			r.Get("/devices/{deviceID}", deviceH.Get)
			r.Get("/devices/{deviceID}/frames", frameH.ListForDevice)
			r.Get("/devices/{deviceID}/metrics", readingsH.Metrics)
			r.Get("/devices/{deviceID}/readings", readingsH.History)
			r.Get("/devices/{deviceID}/readings/export", readingsH.Export)
			r.Get("/zones", zoneH.List)

			r.Route("/reminders", func(r chi.Router) {
				r.Get("/", remindersH.List)
				r.Post("/", remindersH.Create)
				r.Patch("/{id}", remindersH.SetCompleted)
				r.Delete("/{id}", remindersH.Delete)
			})

			// Manual control -- telling one device to do something right
			// now. This reaches a real actuator or camera, so a viewer
			// account can look but not touch; admin and operator both can.
			r.Group(func(r chi.Router) {
				r.Use(RequireAnyRole("admin", "operator"))
				r.Post("/devices/{deviceID}/command", commandH.Send)
			})

			// Handing out a device credential controls something physical
			// in someone's greenhouse, so it's restricted to admins --
			// same for creating zones and moving a device between them.
			r.Group(func(r chi.Router) {
				r.Use(RequireRole("admin"))
				r.Post("/devices/provision", provisionH.Provision)
				r.Post("/zones", zoneH.Create)
				r.Put("/devices/{deviceID}/zone", zoneH.AssignDevice)
			})
		})
	})

	// Called only by RabbitMQ itself, over the private docker-compose
	// network -- see the long comment on RabbitMQAuthHandler for why
	// this intentionally sits outside user JWT auth.
	r.Route("/internal/rabbitmq/auth", func(r chi.Router) {
		r.Post("/user", rmqAuthH.User)
		r.Post("/vhost", rmqAuthH.Vhost)
		r.Post("/resource", rmqAuthH.Resource)
		r.Post("/topic", rmqAuthH.Topic)
	})

	return r
}

// corsMiddleware allows the Vue dev server / PWA (served from the same LAN,
// possibly a different port) to call the API. Locked to same-origin in
// production once the frontend is served behind the gateway.
func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
		if req.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, req)
	})
}
