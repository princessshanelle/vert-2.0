// Command gateway is VERT's single entrypoint for:
//   - the HTTP API consumed by the Vue PWA (auth, device registry, dashboards)
//   - the AMQP consumers that turn device telemetry into time-series rows
//     and device lifecycle events into registry/status updates.
//   - the object-storage-backed camera frame upload/view endpoints.
//   - the webhook RabbitMQ calls to authenticate and authorize devices.
//
// It intentionally does not talk MQTT directly: devices publish over MQTT
// to RabbitMQ's MQTT plugin, which re-exposes those messages on amq.topic
// for this service to consume over AMQP.
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

	"github.com/vert-project/gateway/internal/api"
	"github.com/vert-project/gateway/internal/config"
	"github.com/vert-project/gateway/internal/mq"
	"github.com/vert-project/gateway/internal/storage"
	"github.com/vert-project/gateway/internal/store"
	"github.com/vert-project/gateway/internal/timeseries"
	"github.com/vert-project/gateway/internal/ws"
)

func main() {
	cfg := config.FromEnv()

	// docker-compose starts every container roughly at once, so Postgres
	// and RabbitMQ are very often not accepting connections yet on the
	// gateway's first attempt. Retrying with a short backoff, rather than
	// crash-looping, is what makes `docker compose up` "just work" on the
	// first try instead of needing a human to notice and restart it.
	db, err := connectWithRetry("postgres", 15, 2*time.Second, func() (*store.DB, error) {
		return store.Connect(context.Background(), cfg.DBURL)
	})
	if err != nil {
		log.Fatalf("connect postgres: %v", err)
	}
	defer db.Close()

	tsdb, err := connectWithRetry("timescaledb", 15, 2*time.Second, func() (*timeseries.DB, error) {
		return timeseries.Connect(context.Background(), cfg.TSDBURL)
	})
	if err != nil {
		log.Fatalf("connect timescaledb: %v", err)
	}
	defer tsdb.Close()

	objects, err := connectWithRetry("object storage", 15, 2*time.Second, func() (*storage.ObjectStore, error) {
		return storage.New(context.Background(), cfg)
	})
	if err != nil {
		log.Fatalf("connect object storage: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Runs for the lifetime of the process, reconnecting and re-declaring
	// everything whenever the AMQP connection drops -- not just once at
	// startup. A host going to sleep, RabbitMQ restarting, or a network
	// blip all close this connection out from under us; without this loop
	// the consumer goroutines below would quietly exit once and never
	// come back, while the HTTP API kept answering requests as if nothing
	// were wrong -- exactly the "nothing updates, no error anywhere"
	// failure mode this is here to prevent.
	commander := mq.NewCommander()
	hub := ws.NewHub()
	go hub.Run(ctx)
	go runMessaging(ctx, cfg, db, tsdb, commander, hub)

	router := api.NewRouter(db, tsdb, objects, commander, hub, cfg)
	srv := &http.Server{Addr: cfg.HTTPAddr, Handler: router}

	go func() {
		log.Printf("gateway HTTP API listening on %s", cfg.HTTPAddr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server: %v", err)
		}
	}()

	<-ctx.Done()
	log.Println("shutting down")
	_ = srv.Shutdown(context.Background())
}

// runMessaging owns the gateway's entire relationship with RabbitMQ:
// connect, declare topology, run the telemetry and lifecycle consumers,
// and -- the part that was missing before -- notice when the connection
// dies and start over. Each pass gets its own connection and its own
// consumer goroutines; the previous pass's goroutines exit on their own
// once their delivery channel closes.
func runMessaging(ctx context.Context, cfg config.Config, db *store.DB, tsdb *timeseries.DB, commander *mq.Commander, hub *ws.Hub) {
	for {
		if ctx.Err() != nil {
			return
		}

		conn, err := connectWithRetry("rabbitmq", 15, 2*time.Second, func() (*amqp.Connection, error) {
			return mq.Connect(cfg.AMQPURL)
		})
		if err != nil {
			// Never give up: returning here left the process alive (so the
			// container restart policy never fired) but deaf to every device
			// until someone restarted it by hand. A RabbitMQ restart can
			// take longer than the 30 s connectWithRetry allows.
			log.Printf("mq: rabbitmq still unreachable, will keep trying: %v", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(10 * time.Second):
			}
			continue
		}

		if err := mq.DeclareTopology(conn); err != nil {
			log.Printf("mq: declare topology: %v", err)
			conn.Close()
			time.Sleep(2 * time.Second)
			continue
		}

		if err := commander.Attach(conn); err != nil {
			log.Printf("mq: open command channel: %v", err)
			conn.Close()
			time.Sleep(2 * time.Second)
			continue
		}

		closed := make(chan *amqp.Error, 1)
		conn.NotifyClose(closed)

		consumerCtx, stopConsumers := context.WithCancel(ctx)
		// A consumer can also stop on its own while the connection stays
		// up (its channel closed by the broker, say). Closing the
		// connection then sends us round the reconnect loop below instead
		// of leaving that consumer silently gone.
		go func() { mq.RunTelemetryConsumer(consumerCtx, conn, db, tsdb, hub); conn.Close() }()
		go func() { mq.RunLifecycleConsumer(consumerCtx, conn, db); conn.Close() }()

		log.Println("mq: connected, consuming telemetry and lifecycle events")

		select {
		case <-ctx.Done():
			stopConsumers()
			conn.Close()
			return
		case amqpErr := <-closed:
			stopConsumers()
			if amqpErr == nil {
				log.Println("mq: a consumer stopped, reconnecting")
			} else {
				log.Printf("mq: connection to rabbitmq lost, reconnecting: %v", amqpErr)
			}
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
