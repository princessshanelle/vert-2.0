"""Live MJPEG view on :81/stream -- the same URL and format the ESP32
camera serves, so the dashboard's existing eye-icon toggle works as-is.

Like the ESP32, the stream is off until a `start_stream` command arrives
(nobody is watched by default). Unlike the ESP32, it doesn't interrupt
analysis, and it switches itself back off after `idle_timeout_s` with no
viewers -- closing a dashboard tab never sends `stop_stream`.

Each frame is encoded once and shared by every viewer, so a second
viewer costs bandwidth, not CPU.

Endpoints: /stream (MJPEG), /snapshot.jpg, /healthz (JSON, always on).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import numpy as np

from .camera import Camera
from .config import StreamCfg
from .overlay import encode_jpeg

log = logging.getLogger("plant-cam.stream")

_BOUNDARY = "vertframeboundary"


class StreamServer:
    def __init__(self, cfg: StreamCfg, camera: Camera, render: Callable[[np.ndarray], np.ndarray],
                 status: Callable[[], dict]):
        self.cfg = cfg
        self.camera = camera
        self.render = render  # RGB frame -> annotated BGR frame
        self.status = status
        self._lock = threading.Lock()
        self._requested = False
        self._clients = 0
        self._idle_since = time.monotonic()
        self._cache_seq = -1
        self._cache_jpeg = b""
        self._httpd: ThreadingHTTPServer | None = None

    # ---- on/off ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.cfg.always_on or self._requested

    @property
    def clients(self) -> int:
        return self._clients

    def request_start(self) -> None:
        with self._lock:
            self._requested = True
            self._idle_since = time.monotonic()
        log.info("live view on")

    def request_stop(self) -> None:
        with self._lock:
            self._requested = False
        log.info("live view off")

    def tick(self) -> None:
        """Called once a second by the app: switches an unwatched stream off."""
        with self._lock:
            idle = self._clients == 0 and time.monotonic() - self._idle_since > self.cfg.idle_timeout_s
            if self._requested and idle:
                self._requested = False
                log.info("live view off (no viewers for %.0fs)", self.cfg.idle_timeout_s)

    # ---- serving --------------------------------------------------------

    def start(self) -> None:
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.cfg.port), handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, name="stream", daemon=True).start()
        log.info("stream server listening on :%d", self.cfg.port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()

    def jpeg_for(self, frame: np.ndarray, seq: int) -> bytes:
        with self._lock:
            if seq != self._cache_seq:
                bgr = self.render(frame) if self.cfg.overlay else frame[:, :, ::-1]
                self._cache_jpeg = encode_jpeg(bgr, self.cfg.jpeg_quality)
                self._cache_seq = seq
            return self._cache_jpeg

    def _client_joined(self) -> None:
        with self._lock:
            self._clients += 1

    def _client_left(self) -> None:
        with self._lock:
            self._clients -= 1
            self._idle_since = time.monotonic()

    def wait_enabled(self, timeout: float) -> bool:
        # The dashboard sends start_stream and loads the <img> at the same
        # moment; give the command a few seconds to arrive before refusing.
        deadline = time.monotonic() + timeout
        while not self.enabled and time.monotonic() < deadline:
            time.sleep(0.1)
        return self.enabled


def _make_handler(server: StreamServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # route through logging, quietly
            log.debug("%s %s", self.address_string(), fmt % args)

        def do_GET(self):  # noqa: N802 -- http.server's naming
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._send_json(200, server.status())
            elif path == "/snapshot.jpg":
                self._snapshot()
            elif path == "/stream":
                self._stream()
            else:
                self._send_json(404, {"error": "not found"})

        def _send_json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self) -> None:
            self._send_json(503, {"error": "live view is off -- send this device a start_stream command"})

        def _snapshot(self) -> None:
            if not server.wait_enabled(3.0):
                return self._refuse()
            frame, seq = server.camera.latest()
            if frame is None:
                return self._send_json(503, {"error": "no frame yet"})
            jpeg = server.jpeg_for(frame, seq)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)

        def _stream(self) -> None:
            if not server.wait_enabled(3.0):
                return self._refuse()
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace;boundary={_BOUNDARY}")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            server._client_joined()
            min_gap = 1.0 / max(server.cfg.max_fps, 0.1)
            seq = 0
            try:
                while server.enabled:
                    started = time.monotonic()
                    frame, seq = server.camera.wait_next(seq, timeout=2.0)
                    if frame is None:
                        continue
                    jpeg = server.jpeg_for(frame, seq)
                    self.wfile.write(
                        f"\r\n--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(jpeg)
                    remaining = min_gap - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # viewer went away -- normal
            finally:
                server._client_left()

    return Handler
