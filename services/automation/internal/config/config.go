package config

import "os"

type Config struct {
	AMQPURL   string
	DBURL     string
	JWTSecret string
	HTTPAddr  string
}

func FromEnv() Config {
	return Config{
		AMQPURL:   getenv("VERT_AMQP_URL", "amqp://vert_automation:vert_automation_dev_password@localhost:5672/"),
		DBURL:     getenv("VERT_DB_URL", "postgres://vert:vert_dev_password@localhost:5432/vert?sslmode=disable"),
		JWTSecret: getenv("VERT_JWT_SECRET", "dev-secret-change-me"),
		HTTPAddr:  getenv("VERT_HTTP_ADDR", ":8081"),
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
