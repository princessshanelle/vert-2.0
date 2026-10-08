package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/automation/internal/engine"
	"github.com/vert-project/automation/internal/store"
)

type RulesHandler struct {
	DB     *store.DB
	Engine *engine.Engine
}

func (h *RulesHandler) List(w http.ResponseWriter, r *http.Request) {
	rules, err := h.DB.ListAllRules(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list rules")
		return
	}
	writeJSON(w, http.StatusOK, rules)
}

func (h *RulesHandler) Create(w http.ResponseWriter, r *http.Request) {
	var rule store.Rule
	if err := json.NewDecoder(r.Body).Decode(&rule); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if rule.Name == "" || len(rule.Condition) == 0 || len(rule.Action) == 0 {
		writeError(w, http.StatusBadRequest, "name, condition, and action are all required")
		return
	}
	if rule.ZoneID == nil && rule.DeviceID == nil {
		writeError(w, http.StatusBadRequest, "a rule needs either a zone_id or a device_id to know what it watches")
		return
	}
	rule.Enabled = true

	created, err := h.DB.CreateRule(r.Context(), rule)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not create rule")
		return
	}
	h.Engine.RefreshRules(r.Context())
	writeJSON(w, http.StatusCreated, created)
}

func (h *RulesHandler) SetEnabled(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	var body struct {
		Enabled bool `json:"enabled"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.DB.SetRuleEnabled(r.Context(), id, body.Enabled); err != nil {
		writeError(w, http.StatusInternalServerError, "could not update rule")
		return
	}
	h.Engine.RefreshRules(r.Context())
	w.WriteHeader(http.StatusNoContent)
}

func (h *RulesHandler) Delete(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if err := h.DB.DeleteRule(r.Context(), id); err != nil {
		writeError(w, http.StatusInternalServerError, "could not delete rule")
		return
	}
	h.Engine.RefreshRules(r.Context())
	w.WriteHeader(http.StatusNoContent)
}
