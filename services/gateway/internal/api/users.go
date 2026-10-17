package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"golang.org/x/crypto/bcrypt"

	"github.com/vert-project/gateway/internal/store"
)

// UsersHandler is account management for admins: who can use this
// greenhouse and with which role. Accounts are never deleted -- devices,
// reminders and alerts point at the person who made them -- so "remove"
// means disabling, which is reversible and ends their sessions at once.
type UsersHandler struct {
	DB *store.DB
}

var validRoles = map[string]bool{"admin": true, "operator": true, "viewer": true}

var (
	errSelfChange = errors.New("you can't change your own role or remove your own access -- ask another admin")
	errLastAdmin  = errors.New("this is the last admin -- make someone else an admin first")
	errBadRole    = errors.New("role must be admin, operator or viewer")
)

// checkAccountChange is the safety rule for changing someone's role or
// access, kept free of HTTP and the database so it can be tested on its own:
// nobody edits their own account here (that's what Settings is for), and
// the last active admin can never be demoted or disabled, or nobody could
// manage accounts any more.
func checkAccountChange(actorID string, target *store.User, newRole *string, disable *bool, activeAdmins int) error {
	if newRole != nil && !validRoles[*newRole] {
		return errBadRole
	}
	if target.ID.String() == actorID {
		return errSelfChange
	}
	losesAdmin := target.Role == "admin" && !target.Disabled &&
		((newRole != nil && *newRole != "admin") || (disable != nil && *disable))
	if losesAdmin && activeAdmins <= 1 {
		return errLastAdmin
	}
	return nil
}

func (h *UsersHandler) List(w http.ResponseWriter, r *http.Request) {
	users, err := h.DB.ListUsers(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list accounts")
		return
	}
	writeJSON(w, http.StatusOK, users)
}

type createUserRequest struct {
	Email       string `json:"email"`
	DisplayName string `json:"display_name"`
	Role        string `json:"role"`
	Password    string `json:"password"`
}

func (h *UsersHandler) Create(w http.ResponseWriter, r *http.Request) {
	var req createUserRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	req.Email = normaliseEmail(req.Email)
	req.DisplayName = strings.TrimSpace(req.DisplayName)
	switch {
	case !validEmail(req.Email):
		writeError(w, http.StatusBadRequest, "that doesn't look like an email address")
		return
	case req.DisplayName == "":
		writeError(w, http.StatusBadRequest, "a name is required")
		return
	case !validRoles[req.Role]:
		writeError(w, http.StatusBadRequest, errBadRole.Error())
		return
	case !validPassword(req.Password):
		writeError(w, http.StatusBadRequest, "the temporary password must be at least 8 characters")
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not hash password")
		return
	}
	user, err := h.DB.CreateUserWithRole(r.Context(), req.Email, string(hash), req.DisplayName, req.Role)
	if err != nil {
		writeError(w, http.StatusConflict, "an account with that email already exists")
		return
	}
	writeJSON(w, http.StatusCreated, user)
}

type updateUserRequest struct {
	Role     *string `json:"role"`
	Disabled *bool   `json:"disabled"`
}

func (h *UsersHandler) Update(w http.ResponseWriter, r *http.Request) {
	var req updateUserRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || (req.Role == nil && req.Disabled == nil) {
		writeError(w, http.StatusBadRequest, "send a role and/or disabled")
		return
	}
	target, ok := h.target(w, r)
	if !ok {
		return
	}
	admins, err := h.DB.CountActiveAdmins(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not check admins")
		return
	}
	if err := checkAccountChange(ClaimsFromContext(r.Context()).UserID, target, req.Role, req.Disabled, admins); err != nil {
		status := http.StatusConflict
		if errors.Is(err, errBadRole) {
			status = http.StatusBadRequest
		}
		writeError(w, status, err.Error())
		return
	}
	updated := target
	if req.Role != nil {
		if updated, err = h.DB.SetRole(r.Context(), target.ID.String(), *req.Role); err != nil {
			writeError(w, http.StatusInternalServerError, "could not change the role")
			return
		}
	}
	if req.Disabled != nil {
		if updated, err = h.DB.SetDisabled(r.Context(), target.ID.String(), *req.Disabled); err != nil {
			writeError(w, http.StatusInternalServerError, "could not change access")
			return
		}
	}
	writeJSON(w, http.StatusOK, updated)
}

type resetPasswordRequest struct {
	NewPassword string `json:"new_password"`
}

// ResetPassword is for someone who forgot theirs: the admin sets a
// temporary one and tells them, and every session of that account ends.
func (h *UsersHandler) ResetPassword(w http.ResponseWriter, r *http.Request) {
	var req resetPasswordRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || !validPassword(req.NewPassword) {
		writeError(w, http.StatusBadRequest, "the new password must be at least 8 characters")
		return
	}
	target, ok := h.target(w, r)
	if !ok {
		return
	}
	if target.ID.String() == ClaimsFromContext(r.Context()).UserID {
		writeError(w, http.StatusConflict, "change your own password in Settings")
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.NewPassword), bcrypt.DefaultCost)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not hash password")
		return
	}
	updated, err := h.DB.SetPassword(r.Context(), target.ID.String(), string(hash))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not reset the password")
		return
	}
	writeJSON(w, http.StatusOK, updated)
}

func (h *UsersHandler) target(w http.ResponseWriter, r *http.Request) (*store.User, bool) {
	user, err := h.DB.GetUserByID(r.Context(), chi.URLParam(r, "userID"))
	if err != nil {
		writeError(w, http.StatusNotFound, "no such account")
		return nil, false
	}
	return user, true
}
