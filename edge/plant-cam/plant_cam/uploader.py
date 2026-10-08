"""Uploads a JPEG to the gateway's frame store -- the same endpoint and
the same HTTP Basic credential the ESP32 camera uses -- and returns the
storage reference the gateway assigned (or None).

No MQTT `frame` event is published afterwards on purpose: that event is
what triggers services/inference, and this device has already done its
own analysis.
"""
from __future__ import annotations

import logging

import requests

from .config import Config

log = logging.getLogger("plant-cam.upload")


class Uploader:
    def __init__(self, cfg: Config):
        self.url = f"{cfg.gateway.url}/api/v1/devices/{cfg.device.id}/frames"
        self.auth = (cfg.device.id, cfg.device.secret)
        self.timeout = cfg.gateway.timeout_s
        self.session = requests.Session()

    def upload(self, jpeg: bytes, attempts: int = 2) -> str | None:
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.post(
                    self.url, data=jpeg, auth=self.auth, timeout=self.timeout,
                    headers={"Content-Type": "image/jpeg"},
                )
                if resp.status_code in (200, 201):
                    return resp.text.strip()
                if resp.status_code in (401, 403):
                    log.error("gateway rejected our credentials (HTTP %d) -- check device.secret", resp.status_code)
                    return None
                log.warning("frame upload failed, HTTP %d: %s", resp.status_code, resp.text[:200])
            except requests.RequestException as exc:
                log.warning("frame upload attempt %d/%d failed: %s", attempt, attempts, exc)
        return None
