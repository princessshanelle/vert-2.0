package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/automation/internal/store"
)

type AlertsHandler struct {
	DB *store.DB
}

func (h *AlertsHandler) List(w http.ResponseWriter, r *http.Request) {
	alerts, err := h.DB.ListAlerts(r.Context(), 100)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list alerts")
		return
	}
	// Never make the frontend guess: an empty greenhouse is good news,
	// so send an explicit empty list rather than null.
	if alerts == nil {
		alerts = []store.Alert{}
	}
	writeJSON(w, http.StatusOK, alerts)
}

// Acknowledge lets anyone signed in mark an alert as seen -- a family
// member checking the dashboard shouldn't need an operator account just
// to say "yes, I noticed, thank you."
func (h *AlertsHandler) Acknowledge(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if err := h.DB.AcknowledgeAlert(r.Context(), id); err != nil {
		writeError(w, http.StatusInternalServerError, "could not acknowledge alert")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
