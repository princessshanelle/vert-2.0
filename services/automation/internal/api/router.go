package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/vert-project/automation/internal/config"
	"github.com/vert-project/automation/internal/engine"
	"github.com/vert-project/automation/internal/store"
)

func NewRouter(db *store.DB, eng *engine.Engine, cfg config.Config) http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(corsMiddleware)

	rulesH := &RulesHandler{DB: db, Engine: eng}
	alertsH := &AlertsHandler{DB: db}

	r.Route("/api/v1/rules", func(r chi.Router) {
		r.Use(RequireAuth(cfg.JWTSecret))
		r.Get("/", rulesH.List)

		r.Group(func(r chi.Router) {
			r.Use(RequireAnyRole("admin", "operator"))
			r.Post("/", rulesH.Create)
			r.Patch("/{id}", rulesH.SetEnabled)
			r.Delete("/{id}", rulesH.Delete)
		})
	})

	r.Route("/api/v1/alerts", func(r chi.Router) {
		r.Use(RequireAuth(cfg.JWTSecret))
		r.Get("/", alertsH.List)
		r.Post("/{id}/acknowledge", alertsH.Acknowledge)
	})

	return r
}
