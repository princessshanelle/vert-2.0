"""Live MJPEG view on :81/stream -- the URL and format the ESP32 camera
serves, so the dashboard's existing eye-icon toggle works as-is.

Served from the 640x480 lores stream, so watching costs little. It is off
until a `start_stream` command arrives and switches itself off after
`idle_timeout_s` with no viewers (closing a dashboard tab never sends
`stop_stream`). Each frame is encoded once and shared by every viewer.

Endpoints: /stream (MJPEG), /snapshot.jpg, /healthz (JSON, always on).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..capture.base import LatestSlot
from ..config import StreamCfg
from ..core.types import Image
from .overlay import encode_jpeg

log = logging.getLogger("plant-cam.stream")

_BOUNDARY = "vertframeboundary"


class StreamServer:
    def __init__(
        self,
        cfg: StreamCfg,
        live: LatestSlot,
        render: Callable[[Image], Image],
        status: Callable[[], dict[str, Any]],
        fps_cap: Callable[[], float | None],
    ) -> None:
        self.cfg = cfg
        self.live = live
        self.render = render  # RGB frame -> annotated BGR frame
        self.status = status
        self.fps_cap = fps_cap
        self._lock = threading.Lock()
        self._requested = False
        self._clients = 0
        self._idle_since = time.monotonic()
        self._cache_seq = -1
        self._cache_jpeg = b""
        self._httpd: ThreadingHTTPServer | None = None
        self.port = cfg.port

    @property
    def enabled(self) -> bool:
        cap = self.fps_cap()
        if cap is not None and cap <= 0:
            return False  # too hot: live view off
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
        with self._lock:
            idle = self._clients == 0 and time.monotonic() - self._idle_since > self.cfg.idle_timeout_s
            if self._requested and idle:
                self._requested = False
                log.info("live view off (no viewers)", extra={"fields": {"idle_s": self.cfg.idle_timeout_s}})

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer((self.cfg.bind, self.cfg.port), _make_handler(self))
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, name="stream", daemon=True).start()
        log.info("stream server listening", extra={"fields": {"port": self.port}})

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    def jpeg_for(self, frame: Image, seq: int) -> bytes:
        with self._lock:
            if seq != self._cache_seq:
                bgr = self.render(frame) if self.cfg.overlay else frame[:, :, ::-1]
                self._cache_jpeg = encode_jpeg(bgr, self.cfg.jpeg_quality)
                self._cache_seq = seq
            return self._cache_jpeg

    def _joined(self) -> None:
        with self._lock:
            self._clients += 1

    def _left(self) -> None:
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


def _make_handler(server: StreamServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), format % args)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._json(200, server.status())
            elif path == "/snapshot.jpg":
                self._snapshot()
            elif path == "/stream":
                self._stream()
            else:
                self._json(404, {"error": "not found"})

        def _json(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self) -> None:
            self._json(503, {"error": "live view is off -- send this device a start_stream command"})

        def _snapshot(self) -> None:
            if not server.wait_enabled(3.0):
                return self._refuse()
            frame, seq = server.live.latest()
            if frame is None:
                return self._json(503, {"error": "no frame yet"})
            jpeg = server.jpeg_for(frame, seq)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)
            return None

        def _stream(self) -> None:
            if not server.wait_enabled(3.0):
                return self._refuse()
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace;boundary={_BOUNDARY}")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            server._joined()
            seq = 0
            try:
                while server.enabled:
                    started = time.monotonic()
                    frame, seq = server.live.wait_newer(seq, timeout=2.0)
                    if frame is None:
                        continue
                    jpeg = server.jpeg_for(frame, seq)
                    self.wfile.write(
                        f"\r\n--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(jpeg)
                    cap = server.fps_cap()
                    fps = min(server.cfg.max_fps, cap) if cap else server.cfg.max_fps
                    remaining = 1.0 / max(fps, 0.1) - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # viewer went away -- normal
            finally:
                server._left()
            return None

    return Handler
