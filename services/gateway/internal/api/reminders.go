package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/gateway/internal/store"
)

type RemindersHandler struct {
	DB *store.DB
}

type createReminderRequest struct {
	Title       string  `json:"title"`
	Description string  `json:"description"`
	DueAt       string  `json:"due_at"` // RFC3339, e.g. "2026-09-20T09:00:00Z"
	Recurrence  *string `json:"recurrence,omitempty"`
}

func (h *RemindersHandler) Create(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFromContext(r.Context())
	if claims == nil {
		writeError(w, http.StatusUnauthorized, "not authenticated")
		return
	}

	var req createReminderRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	req.Title = strings.TrimSpace(req.Title)
	if req.Title == "" {
		writeError(w, http.StatusBadRequest, "give the reminder a short title, like \"reorder pH strips\"")
		return
	}
	dueAt, err := time.Parse(time.RFC3339, req.DueAt)
	if err != nil {
		writeError(w, http.StatusBadRequest, "due_at must be a date and time, e.g. 2026-09-20T09:00:00Z")
		return
	}

	rem, err := h.DB.CreateReminder(r.Context(), claims.UserID, req.Title, req.Description, dueAt, req.Recurrence)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not save that reminder")
		return
	}
	writeJSON(w, http.StatusCreated, rem)
}

func (h *RemindersHandler) List(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFromContext(r.Context())
	reminders, err := h.DB.ListReminders(r.Context(), claims.UserID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list reminders")
		return
	}
	if reminders == nil {
		reminders = []store.Reminder{}
	}
	writeJSON(w, http.StatusOK, reminders)
}

func (h *RemindersHandler) SetCompleted(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFromContext(r.Context())
	id := chi.URLParam(r, "id")

	var body struct {
		Completed bool `json:"completed"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.DB.SetReminderCompleted(r.Context(), id, claims.UserID, body.Completed); err != nil {
		writeError(w, http.StatusInternalServerError, "could not update that reminder")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (h *RemindersHandler) Delete(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFromContext(r.Context())
	id := chi.URLParam(r, "id")
	if err := h.DB.DeleteReminder(r.Context(), id, claims.UserID); err != nil {
		writeError(w, http.StatusInternalServerError, "could not delete that reminder")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
