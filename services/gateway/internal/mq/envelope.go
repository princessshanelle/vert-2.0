package mq

import "time"

// Envelope is the message contract every device and service publishes
// against. Consumers should reject/upgrade unknown SchemaVersion values
// rather than guessing at payload shape.
type Envelope struct {
	SchemaVersion string         `json:"schema_version"`
	DeviceID      string         `json:"device_id"`
	DeviceType    string         `json:"device_type"`
	Zone          string         `json:"zone"`
	Timestamp     time.Time      `json:"ts"`
	Sequence      uint64         `json:"seq"`
	Payload       map[string]any `json:"payload"`
}

const CurrentSchemaVersion = "1.0"
