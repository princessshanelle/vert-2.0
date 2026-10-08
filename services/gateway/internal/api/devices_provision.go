package api

import (
	"crypto/rand"
	"encoding/base32"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/vert-project/gateway/internal/store"
)

type DeviceProvisionHandler struct {
	DB *store.DB
}

type provisionRequest struct {
	DeviceID   string `json:"device_id"`
	DeviceType string `json:"device_type"`
}

type provisionResponse struct {
	DeviceID string `json:"device_id"`
	Secret   string `json:"secret"`
	Note     string `json:"note"`
}

// Provision issues a brand-new device its identity: a device_id and a
// one-time-visible secret. Whoever is physically wiring up the sensor
// copies these straight into the firmware's config.h and never needs to
// see this screen again -- that's the whole point of doing this before
// the device ever powers on, rather than making the grower configure
// anything from the greenhouse itself.
func (h *DeviceProvisionHandler) Provision(w http.ResponseWriter, r *http.Request) {
	var req provisionRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	req.DeviceID = strings.TrimSpace(req.DeviceID)
	req.DeviceType = strings.TrimSpace(req.DeviceType)
	if req.DeviceID == "" || req.DeviceType == "" {
		writeError(w, http.StatusBadRequest, "device_id and device_type are required")
		return
	}

	secret, err := randomSecret()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not generate a device secret")
		return
	}

	if err := h.DB.EnsureDevice(r.Context(), req.DeviceID, req.DeviceType); err != nil {
		writeError(w, http.StatusInternalServerError, "could not register device")
		return
	}

	claims := ClaimsFromContext(r.Context())
	issuedBy := ""
	if claims != nil {
		issuedBy = claims.UserID
	}
	if err := h.DB.IssueDeviceCredential(r.Context(), req.DeviceID, secret, issuedBy); err != nil {
		writeError(w, http.StatusInternalServerError, "could not issue device credential")
		return
	}

	writeJSON(w, http.StatusCreated, provisionResponse{
		DeviceID: req.DeviceID,
		Secret:   secret,
		Note:     "This secret is shown once. Copy it into the device's config.h now -- there is no way to retrieve it again, only to re-provision and issue a new one.",
	})
}

// randomSecret produces a 32-byte, base32-encoded token: high entropy,
// and safe to type by hand into firmware if it ever comes to that.
func randomSecret() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(b), nil
}
