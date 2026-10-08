package api

import (
	"bytes"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/vert-project/gateway/internal/storage"
	"github.com/vert-project/gateway/internal/store"
)

// FrameHandler receives camera uploads (behind RequireDeviceAuth) and
// stores them in MinIO/S3, and serves them back out to the dashboard
// (behind RequireAuth) as short-lived, presigned links.
type FrameHandler struct {
	DB      *store.DB
	Objects *storage.ObjectStore
}

const maxFrameSize = 5 << 20 // 5MB: generous for a JPEG off a XIAO camera, small enough to keep a slow LAN link from choking on one upload

func (h *FrameHandler) Upload(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")

	contentType := r.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "image/jpeg"
	}

	body := http.MaxBytesReader(w, r.Body, maxFrameSize)
	data, err := io.ReadAll(body)
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "frame too large (limit 5MB)")
		return
	}

	objectKey := deviceID + "/" + uuid.NewString() + ".jpg"
	if err := h.Objects.Put(r.Context(), objectKey, bytes.NewReader(data), int64(len(data)), contentType); err != nil {
		writeError(w, http.StatusInternalServerError, "could not store the frame -- please retry")
		return
	}

	if err := h.DB.CreateFrame(r.Context(), deviceID, objectKey, contentType, int64(len(data))); err != nil {
		writeError(w, http.StatusInternalServerError, "frame was stored but could not be recorded")
		return
	}

	w.Header().Set("Content-Type", "text/plain")
	w.WriteHeader(http.StatusCreated)
	_, _ = w.Write([]byte(objectKey))
}

// ListForDevice lets the dashboard show a grower the recent photos of a
// given plant/zone, each as a link that actually opens -- not just a
// device id and a timestamp.
func (h *FrameHandler) ListForDevice(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")
	frames, err := h.DB.ListFramesByDevice(r.Context(), deviceID, 25)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list frames")
		return
	}

	type frameView struct {
		ID        string    `json:"id"`
		CreatedAt time.Time `json:"created_at"`
		URL       string    `json:"url"`
	}
	// The address the browser used to reach us, through the frontend's
	// nginx (which passes the original Host along) -- see PresignedGetURL.
	browserHost := r.Header.Get("X-Forwarded-Host")
	if browserHost == "" {
		browserHost = r.Host
	}
	out := make([]frameView, 0, len(frames))
	for _, f := range frames {
		url, err := h.Objects.PresignedGetURL(r.Context(), f.ObjectKey, 15*time.Minute, browserHost)
		if err != nil {
			// A skipped photo with no explanation anywhere is exactly the
			// kind of silent failure that turns into a confusing bug
			// report later -- log it so the cause is visible immediately.
			log.Printf("frames: could not build a viewing link for %s (device %s): %v", f.ObjectKey, deviceID, err)
			continue
		}
		out = append(out, frameView{ID: f.ID, CreatedAt: f.CreatedAt, URL: url})
	}
	writeJSON(w, http.StatusOK, out)
}
