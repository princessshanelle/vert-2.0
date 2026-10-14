"""Minimal systemd sd_notify (READY, WATCHDOG, STOPPING) with no extra
dependency; a no-op when not run under systemd."""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger("plant-cam.watchdog")


def notify(message: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
    except OSError as exc:
        log.debug("sd_notify failed: %s", exc)
