// Command automation is VERT's rules engine: it watches telemetry for
// sustained threshold breaches (see internal/engine) and turns them into
// device commands and human-readable alerts. It is deliberately its own
// service rather than logic bolted onto ingestion, so a bug in a rule's
// evaluation can never back up or crash the pipe that keeps every other
// reading flowing.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/vert-project/automation/internal/api"
	"github.com/vert-project/automation/internal/config"
	"github.com/vert-project/automation/internal/engine"
	"github.com/vert-project/automation/internal/mq"
	"github.com/vert-project/automation/internal/store"
)

func main() {
	cfg := config.FromEnv()

	// See the identical comment in services/gateway/cmd/gateway/main.go:
	// docker-compose brings containers up together, so a short retry loop
	// here beats crash-looping while Postgres/RabbitMQ finish starting.
	db, err := connectWithRetry("postgres", 15, 2*time.Second, func() (*store.DB, error) {
		return store.Connect(context.Background(), cfg.DBURL)
	})
	if err != nil {
		log.Fatalf("connect postgres: %v", err)
	}
	defer db.Close()

	eng := engine.New(db, nil) // publisher is attached once runMessaging connects

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	eng.RefreshRules(ctx)
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				eng.RefreshRules(ctx)
			}
		}
	}()

	// Runs for the lifetime of the process, reconnecting and re-declaring
	// everything whenever the AMQP connection drops. Without this, a
	// host sleep/wake cycle or a RabbitMQ restart would silently stop
	// rule evaluation forever while the HTTP API (rule CRUD, alerts)
	// kept answering as if nothing were wrong.
	go runMessaging(ctx, cfg, eng)

	router := api.NewRouter(db, eng, cfg)
	srv := &http.Server{Addr: cfg.HTTPAddr, Handler: router}

	go func() {
		log.Printf("automation HTTP API listening on %s", cfg.HTTPAddr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server: %v", err)
		}
	}()

	<-ctx.Done()
	log.Println("shutting down")
	_ = srv.Shutdown(context.Background())
}

func runMessaging(ctx context.Context, cfg config.Config, eng *engine.Engine) {
	for {
		if ctx.Err() != nil {
			return
		}

		conn, err := connectWithRetry("rabbitmq", 15, 2*time.Second, func() (*amqp.Connection, error) {
			return mq.Connect(cfg.AMQPURL)
		})
		if err != nil {
			log.Printf("mq: giving up on rabbitmq for now: %v", err)
			return
		}

		if err := mq.DeclareTopology(conn); err != nil {
			log.Printf("mq: declare topology: %v", err)
			conn.Close()
			time.Sleep(2 * time.Second)
			continue
		}

		publisher, err := mq.NewPublisher(conn)
		if err != nil {
			log.Printf("mq: open publisher channel: %v", err)
			conn.Close()
			time.Sleep(2 * time.Second)
			continue
		}
		eng.SetPublisher(publisher)

		closed := make(chan *amqp.Error, 1)
		conn.NotifyClose(closed)

		consumerCtx, stopConsumer := context.WithCancel(ctx)
		go mq.RunEvaluationConsumer(consumerCtx, conn, eng.Evaluate)

		log.Println("mq: connected, evaluating telemetry against rules")

		select {
		case <-ctx.Done():
			stopConsumer()
			conn.Close()
			return
		case amqpErr := <-closed:
			stopConsumer()
			log.Printf("mq: connection to rabbitmq lost, reconnecting: %v", amqpErr)
		}
	}
}

func connectWithRetry[T any](name string, attempts int, delay time.Duration, connect func() (T, error)) (T, error) {
	var lastErr error
	for i := 1; i <= attempts; i++ {
		v, err := connect()
		if err == nil {
			return v, nil
		}
		lastErr = err
		log.Printf("waiting for %s (attempt %d/%d): %v", name, i, attempts, err)
		time.Sleep(delay)
	}
	var zero T
	return zero, lastErr
}
