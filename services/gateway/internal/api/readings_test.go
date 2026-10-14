package api

import (
	"testing"
	"time"
)

func TestParseBucket(t *testing.T) {
	cases := []struct {
		raw  string
		want time.Duration
		ok   bool
	}{
		{"", 0, true},
		{"60", time.Minute, true},
		{"3600", time.Hour, true},
		{"86400", 24 * time.Hour, true},
		{"59", 0, false},
		{"86401", 0, false},
		{"-60", 0, false},
		{"1h", 0, false},
	}
	for _, c := range cases {
		got, ok := parseBucket(c.raw)
		if got != c.want || ok != c.ok {
			t.Errorf("parseBucket(%q) = %v, %v; want %v, %v", c.raw, got, ok, c.want, c.ok)
		}
	}
}
