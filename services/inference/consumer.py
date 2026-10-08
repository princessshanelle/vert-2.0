"""RabbitMQ wiring for the inference service: connect (with the same
patient retry the Go services use, since docker-compose starts every
container at once), declare the one queue this service owns, and turn
each camera-frame-ready event into a fetch + analyze + publish cycle.
"""
from __future__ import annotations

import json
import logging
import time

import pika
from pika.exceptions import (
    AMQPConnectionError,
    AMQPHeartbeatTimeout,
    ChannelClosedByBroker,
    ConnectionClosed,
    StreamLostError,
)

from config import Config
from envelope import Envelope, SCHEMA_VERSION
from storage import FrameStore
from vision import analyze

log = logging.getLogger("vert-inference")

# amq.topic is RabbitMQ's built-in topic exchange, and critically the
# ONLY exchange the MQTT plugin ever publishes device messages to or
# delivers device subscriptions from (mqtt.exchange in
# infra/rabbitmq/rabbitmq.conf). Both the camera-frame-ready event this
# service consumes, and the plant-health reading it republishes, have to
# live here -- a separately-declared exchange would never see MQTT
# traffic at all, which is exactly the bug this replaced.
DEVICE_EXCHANGE = "amq.topic"
QUEUE_FRAMES = "q.ai.frames"

_CONNECT_ATTEMPTS = 15
_CONNECT_DELAY_SECONDS = 2


def connect_with_retry(amqp_url: str) -> pika.BlockingConnection:
    last_error: Exception | None = None
    for attempt in range(1, _CONNECT_ATTEMPTS + 1):
        try:
            return pika.BlockingConnection(pika.URLParameters(amqp_url))
        except AMQPConnectionError as exc:
            last_error = exc
            log.info("waiting for rabbitmq (attempt %d/%d): %s", attempt, _CONNECT_ATTEMPTS, exc)
            time.sleep(_CONNECT_DELAY_SECONDS)
    raise RuntimeError(f"could not connect to rabbitmq after {_CONNECT_ATTEMPTS} attempts") from last_error


class InferenceConsumer:
    def __init__(self, cfg: Config, frames: FrameStore):
        self.cfg = cfg
        self.frames = frames
        self.connection: pika.BlockingConnection | None = None
        self.channel = None
        self._stopping = False

    # start() reconnects for as long as the process is alive. Without
    # this loop, the very first heartbeat timeout or dropped TCP
    # connection (a host going to sleep, RabbitMQ restarting, a network
    # blip) would raise out of start_consuming() uncaught, crash the
    # process, and -- with no restart policy watching it -- leave the
    # whole plant-health pipeline dark until someone noticed and
    # restarted it by hand.
    def start(self) -> None:
        while not self._stopping:
            try:
                self._run_once()
            except (AMQPConnectionError, AMQPHeartbeatTimeout, ChannelClosedByBroker,
                     ConnectionClosed, StreamLostError) as exc:
                if self._stopping:
                    break
                log.warning("lost connection to rabbitmq, reconnecting: %s", exc)
                time.sleep(_CONNECT_DELAY_SECONDS)

    def _run_once(self) -> None:
        self.connection = connect_with_retry(self.cfg.amqp_url)
        self.channel = self.connection.channel()

        # This service only declares and binds the one queue it owns.
        # DEVICE_EXCHANGE (amq.topic) is one of RabbitMQ's built-in
        # exchanges -- it always exists from the moment the broker boots,
        # regardless of whether the gateway has started yet, so there's
        # no "wait for it to be declared" race to handle here.
        self.channel.queue_declare(QUEUE_FRAMES, durable=True)
        self.channel.queue_bind(QUEUE_FRAMES, DEVICE_EXCHANGE, routing_key="vert.*.camera.*.frame")

        self.channel.basic_qos(prefetch_count=4)
        self.channel.basic_consume(QUEUE_FRAMES, on_message_callback=self._on_frame_ready)

        log.info("consuming %s, watching for photos to look in on", QUEUE_FRAMES)
        # Returns normally only once stop() calls stop_consuming() below;
        # any connection loss raises instead, caught by start()'s loop.
        self.channel.start_consuming()

    def stop(self) -> None:
        self._stopping = True
        if self.channel is not None and self.channel.is_open:
            self.channel.stop_consuming()
        if self.connection is not None and self.connection.is_open:
            self.connection.close()

    def _on_frame_ready(self, channel, method, _properties, body: bytes) -> None:
        try:
            msg = json.loads(body)
        except json.JSONDecodeError:
            log.warning("frame-ready event was not valid JSON; dropping it rather than retrying forever")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        if msg.get("schema_version") != SCHEMA_VERSION:
            log.warning("unsupported schema version %r from %s", msg.get("schema_version"), msg.get("device_id"))
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        device_id = msg.get("device_id", "unknown-device")
        zone = msg.get("zone", "")
        image_ref = msg.get("payload", {}).get("image_ref")

        if not image_ref:
            log.warning("frame-ready event from %s had no image_ref; nothing to look at", device_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        image_bytes = self.frames.fetch(image_ref)
        if image_bytes is None:
            # Not retried: the camera takes a fresh photo every capture
            # cycle regardless, so there will be another frame along
            # shortly. Requeuing immediately here would just spin against
            # the same failure with no backoff.
            log.info("frame %s was not available for %s; skipping this one", image_ref, device_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        result = analyze(image_bytes)
        if result is None:
            log.warning("could not read %s as an image for %s; not retrying an unreadable file", image_ref, device_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        out = Envelope(
            device_id=device_id,
            device_type="plant-health-model",
            zone=zone,
            payload=result.to_payload(),
        )
        channel.basic_publish(
            exchange=DEVICE_EXCHANGE,
            routing_key=f"vert.{zone}.plant.{device_id}.reading",
            body=json.dumps(out.to_dict()).encode(),
        )
        log.info("%s (%s): %s", device_id, result.label, result.note)
        channel.basic_ack(delivery_tag=method.delivery_tag)
