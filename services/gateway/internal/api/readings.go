package api

import (
	"encoding/csv"
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/vert-project/gateway/internal/timeseries"
)

type ReadingsHandler struct {
	TS *timeseries.DB
}

// Metrics tells the frontend which chart tabs to offer for a device --
// "temp_c", "humidity_pct", "health_score", whatever that device has
// actually ever reported -- rather than the frontend having to hardcode
// per-device-type assumptions about what fields exist.
func (h *ReadingsHandler) Metrics(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")
	metrics, err := h.TS.Metrics(r.Context(), deviceID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list this device's metrics")
		return
	}
	if metrics == nil {
		metrics = []string{}
	}
	writeJSON(w, http.StatusOK, metrics)
}

// History serves the points behind one chart. ?metric= is required;
// ?minutes= defaults to a reasonable "what's happened recently" window
// rather than forcing every caller to specify it.
func (h *ReadingsHandler) History(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")
	metric := r.URL.Query().Get("metric")
	if metric == "" {
		writeError(w, http.StatusBadRequest, "a metric name is required, e.g. ?metric=temp_c")
		return
	}

	minutes := 60
	if raw := r.URL.Query().Get("minutes"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			minutes = parsed
		}
	}

	points, err := h.TS.History(r.Context(), deviceID, metric, time.Duration(minutes)*time.Minute)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not load chart history")
		return
	}
	if points == nil {
		points = []timeseries.Point{}
	}
	writeJSON(w, http.StatusOK, points)
}

// Export hands back a device's raw history as CSV, every metric it has
// ever reported as its own column, one row per timestamp -- someone
// running a real greenhouse wants to pull this into a spreadsheet or a
// proper analysis tool (correlate against a weather API, compute VPD,
// whatever this dashboard doesn't do), not just eyeball a sparkline. It
// deliberately reuses the same Metrics+History queries the chart tabs
// already use rather than a new query path, so the exported numbers can
// never disagree with what's on screen for the same time range.
func (h *ReadingsHandler) Export(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "deviceID")

	minutes := 1440
	if raw := r.URL.Query().Get("minutes"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			minutes = parsed
		}
	}
	since := time.Duration(minutes) * time.Minute

	metrics, err := h.TS.Metrics(r.Context(), deviceID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not list this device's metrics")
		return
	}
	if len(metrics) == 0 {
		writeError(w, http.StatusNotFound, "this device has no recorded readings yet")
		return
	}

	// Timestamps for different metrics off the same device come from the
	// same reading envelope (see mq.RunTelemetryConsumer), so they align
	// exactly rather than needing a fuzzy time-bucket join -- a row is
	// simply "everything known for one instant."
	rows := make(map[time.Time]map[string]float64)
	for _, metric := range metrics {
		points, err := h.TS.History(r.Context(), deviceID, metric, since)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "could not load history for "+metric)
			return
		}
		for _, p := range points {
			if rows[p.Time] == nil {
				rows[p.Time] = make(map[string]float64)
			}
			rows[p.Time][metric] = p.Value
		}
	}

	timestamps := make([]time.Time, 0, len(rows))
	for ts := range rows {
		timestamps = append(timestamps, ts)
	}
	sort.Slice(timestamps, func(i, j int) bool { return timestamps[i].Before(timestamps[j]) })

	w.Header().Set("Content-Type", "text/csv")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s-readings.csv"`, deviceID))

	out := csv.NewWriter(w)
	header := append([]string{"timestamp"}, metrics...)
	_ = out.Write(header)
	record := make([]string, len(header))
	for _, ts := range timestamps {
		record[0] = ts.UTC().Format(time.RFC3339)
		for i, metric := range metrics {
			if v, ok := rows[ts][metric]; ok {
				record[i+1] = strconv.FormatFloat(v, 'f', -1, 64)
			} else {
				record[i+1] = ""
			}
		}
		_ = out.Write(record)
	}
	out.Flush()
}
