package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/gateway/internal/store"
)

type DeviceHandler struct {
	DB *store.DB
}

func (h *DeviceHandler) List(w http.ResponseWriter, r *http.Request) {
	devices, err := h.DB.ListDevices(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list devices")
		return
	}
	if devices == nil {
		devices = []store.Device{}
	}
	writeJSON(w, http.StatusOK, devices)
}

func (h *DeviceHandler) Get(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")
	device, err := h.DB.GetDevice(r.Context(), deviceID)
	if err != nil {
		writeError(w, http.StatusNotFound, "no device known by that id yet")
		return
	}
	writeJSON(w, http.StatusOK, device)
}
