package api

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/gateway/internal/store"
)

type ZoneHandler struct {
	DB *store.DB
}

func (h *ZoneHandler) List(w http.ResponseWriter, r *http.Request) {
	zones, err := h.DB.ListZones(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list zones")
		return
	}
	writeJSON(w, http.StatusOK, zones)
}

func (h *ZoneHandler) Create(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	if body.Name == "" {
		writeError(w, http.StatusBadRequest, "a zone needs a name -- e.g. \"Zone A\" or \"Nursery\"")
		return
	}
	zone, err := h.DB.CreateZone(r.Context(), body.Name)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not create zone")
		return
	}
	writeJSON(w, http.StatusCreated, zone)
}

// AssignDevice puts a device into a zone -- e.g. after physically moving
// a sensor from the nursery bench into Zone A -- so both the dashboard's
// grouping and any per-zone automation rule pick it up immediately.
func (h *ZoneHandler) AssignDevice(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")
	var body struct {
		ZoneID string `json:"zone_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.ZoneID == "" {
		writeError(w, http.StatusBadRequest, "zone_id is required")
		return
	}
	if err := h.DB.AssignDeviceZone(r.Context(), deviceID, body.ZoneID); err != nil {
		writeError(w, http.StatusInternalServerError, "could not assign zone")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
