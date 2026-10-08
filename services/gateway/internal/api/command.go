package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/gateway/internal/mq"
	"github.com/vert-project/gateway/internal/store"
)

type CommandHandler struct {
	DB        *store.DB
	Commander *mq.Commander
}

type commandRequest struct {
	Command string `json:"command"`
}

// Send is manual control -- a person on the dashboard telling one
// specific device to do something right now, as opposed to the
// automation engine's rule-triggered commands (services/automation).
// Same wire shape, same routing key convention, different trigger.
func (h *CommandHandler) Send(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")

	var req commandRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Command == "" {
		writeError(w, http.StatusBadRequest, "a command name is required, e.g. {\"command\": \"open\"}")
		return
	}

	device, err := h.DB.GetDevice(r.Context(), deviceID)
	if err != nil {
		writeError(w, http.StatusNotFound, "no device known by that id")
		return
	}
	// A device with no zone yet has nowhere meaningful to route a
	// command -- see Section 4.5 on why zone assignment matters.
	if device.ZoneID == nil {
		writeError(w, http.StatusConflict, "this device isn't in a zone yet -- assign one before sending commands")
		return
	}
	zone, err := h.DB.ZoneName(r.Context(), *device.ZoneID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not resolve this device's zone")
		return
	}

	if err := h.Commander.Send(zone, device.DeviceType, deviceID, map[string]any{"command": req.Command}); err != nil {
		writeError(w, http.StatusServiceUnavailable, "could not reach the greenhouse's message broker -- try again in a moment")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
