package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"

	"github.com/vert-project/gateway/internal/store"
)

type AuthHandler struct {
	DB        *store.DB
	JWTSecret string
}

type registerRequest struct {
	Email       string `json:"email"`
	Password    string `json:"password"`
	DisplayName string `json:"display_name"`
	Locale      string `json:"locale"`
}

type authResponse struct {
	Token string `json:"token"`
	User  struct {
		ID          string `json:"id"`
		Email       string `json:"email"`
		DisplayName string `json:"display_name"`
		Locale      string `json:"locale"`
		Role        string `json:"role"`
	} `json:"user"`
}

func (h *AuthHandler) Register(w http.ResponseWriter, r *http.Request) {
	var req registerRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	req.Email = normaliseEmail(req.Email)
	if req.Email == "" || !validPassword(req.Password) {
		writeError(w, http.StatusBadRequest, "email required and password must be at least 8 characters")
		return
	}
	if req.Locale == "" {
		req.Locale = "en"
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not hash password")
		return
	}

	user, err := h.DB.CreateUser(r.Context(), req.Email, string(hash), req.DisplayName, req.Locale)
	if err != nil {
		// Likely a unique-constraint violation on email; treated uniformly
		// so we never leak which accounts exist via a different message.
		writeError(w, http.StatusConflict, "an account with that email already exists")
		return
	}

	h.respondWithToken(w, user)
}

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

func (h *AuthHandler) Login(w http.ResponseWriter, r *http.Request) {
	var req loginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	req.Email = normaliseEmail(req.Email)

	user, err := h.DB.GetUserByEmail(r.Context(), req.Email)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "invalid email or password")
		return
	}
	// A removed account gets the same answer as a wrong password: the
	// login form shouldn't confirm which addresses have accounts.
	if user.Disabled || bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)) != nil {
		writeError(w, http.StatusUnauthorized, "invalid email or password")
		return
	}

	h.respondWithToken(w, user)
}

func (h *AuthHandler) Me(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFromContext(r.Context())
	if claims == nil {
		writeError(w, http.StatusUnauthorized, "not authenticated")
		return
	}
	user, err := h.DB.GetUserByID(r.Context(), claims.UserID)
	if err != nil {
		writeError(w, http.StatusNotFound, "user not found")
		return
	}
	writeJSON(w, http.StatusOK, user)
}

type changePasswordRequest struct {
	CurrentPassword string `json:"current_password"`
	NewPassword     string `json:"new_password"`
}

// ChangePassword ends every other session of this account (token_version
// is bumped) and hands this browser a fresh token so it stays signed in.
func (h *AuthHandler) ChangePassword(w http.ResponseWriter, r *http.Request) {
	var req changePasswordRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	user, ok := h.currentUserWithPassword(w, r, req.CurrentPassword)
	if !ok {
		return
	}
	if !validPassword(req.NewPassword) {
		writeError(w, http.StatusBadRequest, "the new password must be at least 8 characters")
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.NewPassword), bcrypt.DefaultCost)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not hash password")
		return
	}
	updated, err := h.DB.SetPassword(r.Context(), user.ID.String(), string(hash))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not change the password")
		return
	}
	h.respondWithToken(w, updated)
}

type changeEmailRequest struct {
	Email           string `json:"email"`
	CurrentPassword string `json:"current_password"`
}

func (h *AuthHandler) ChangeEmail(w http.ResponseWriter, r *http.Request) {
	var req changeEmailRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	user, ok := h.currentUserWithPassword(w, r, req.CurrentPassword)
	if !ok {
		return
	}
	email := normaliseEmail(req.Email)
	if !validEmail(email) {
		writeError(w, http.StatusBadRequest, "that doesn't look like an email address")
		return
	}
	updated, err := h.DB.SetEmail(r.Context(), user.ID.String(), email)
	if err != nil {
		writeError(w, http.StatusConflict, "an account with that email already exists")
		return
	}
	h.respondWithToken(w, updated)
}

// currentUserWithPassword re-checks the signed-in user's password before a
// change to their own credentials, so an unattended open browser isn't
// enough to take over the account.
func (h *AuthHandler) currentUserWithPassword(w http.ResponseWriter, r *http.Request, password string) (*store.User, bool) {
	claims := ClaimsFromContext(r.Context())
	if claims == nil {
		writeError(w, http.StatusUnauthorized, "not authenticated")
		return nil, false
	}
	user, err := h.DB.GetUserByID(r.Context(), claims.UserID)
	if err != nil {
		writeError(w, http.StatusNotFound, "user not found")
		return nil, false
	}
	if bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(password)) != nil {
		writeError(w, http.StatusForbidden, "your current password is not correct")
		return nil, false
	}
	return user, true
}

func normaliseEmail(s string) string { return strings.ToLower(strings.TrimSpace(s)) }

func validPassword(s string) bool { return len(s) >= 8 }

// validEmail is a sanity check (one @, something either side, a dot in the
// domain), not RFC 5322: it catches typos without rejecting real addresses.
func validEmail(s string) bool {
	at := strings.LastIndex(s, "@")
	return at > 0 && at < len(s)-3 && strings.Count(s, "@") == 1 &&
		strings.Contains(s[at+1:], ".") && !strings.ContainsAny(s, " \t")
}

func (h *AuthHandler) respondWithToken(w http.ResponseWriter, user *store.User) {
	claims := Claims{
		UserID: user.ID.String(),
		Email:  user.Email,
		Role:   user.Role,

		TokenVersion: user.TokenVersion,
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(30 * 24 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString([]byte(h.JWTSecret))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not sign token")
		return
	}

	var resp authResponse
	resp.Token = signed
	resp.User.ID = user.ID.String()
	resp.User.Email = user.Email
	resp.User.DisplayName = user.DisplayName
	resp.User.Locale = user.Locale
	resp.User.Role = user.Role
	writeJSON(w, http.StatusOK, resp)
}
