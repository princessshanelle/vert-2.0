package config

import "os"

type Config struct {
	AMQPURL   string
	DBURL     string
	TSDBURL   string
	JWTSecret string
	HTTPAddr  string

	S3Endpoint       string
	S3PublicEndpoint string
	S3AccessKey      string
	S3SecretKey      string
	S3Bucket         string
	S3UseSSL         bool
}

func FromEnv() Config {
	return Config{
		AMQPURL:   getenv("VERT_AMQP_URL", "amqp://vert_gateway:vert_gateway_dev_password@localhost:5672/"),
		DBURL:     getenv("VERT_DB_URL", "postgres://vert:vert_dev_password@localhost:5432/vert?sslmode=disable"),
		TSDBURL:   getenv("VERT_TS_DB_URL", "postgres://vert:vert_dev_password@localhost:5433/vert_timeseries?sslmode=disable"),
		JWTSecret: getenv("VERT_JWT_SECRET", "dev-secret-change-me"),
		HTTPAddr:  getenv("VERT_HTTP_ADDR", ":8080"),

		// S3Endpoint is how the gateway itself, inside the docker-compose
		// network, reaches MinIO -- used for uploads/bucket setup.
		// S3PublicEndpoint is how a browser (or anything else outside that
		// network) reaches it -- used only when building presigned URLs,
		// since a link containing the internal Docker hostname would be
		// dead the moment it left the container. On a LAN deployment this
		// should be the host machine's LAN IP (matching MQTT_HOST/
		// UPLOAD_HOST in firmware config.h), not "localhost", for anyone
		// opening the dashboard from a different device.
		S3Endpoint:       getenv("VERT_S3_ENDPOINT", "localhost:9000"),
		S3PublicEndpoint: getenv("VERT_S3_PUBLIC_ENDPOINT", getenv("VERT_S3_ENDPOINT", "localhost:9000")),
		S3AccessKey:      getenv("VERT_S3_ACCESS_KEY", "vert_minio_admin"),
		S3SecretKey:      getenv("VERT_S3_SECRET_KEY", "vert_minio_dev_password"),
		S3Bucket:         getenv("VERT_S3_BUCKET", "vert-frames"),
		S3UseSSL:         getenv("VERT_S3_USE_SSL", "false") == "true",
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
