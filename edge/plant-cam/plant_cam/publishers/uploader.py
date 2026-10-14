"""Uploads a JPEG to the gateway's frame store -- the same endpoint and
HTTP Basic credential the ESP32 camera uses -- and returns the object key
the gateway assigned.

No MQTT `frame` event is published afterwards on purpose: that event
triggers services/inference, and this device has already done its own
analysis.
"""

from __future__ import annotations

import logging
import time

import requests

from ..config import Config

log = logging.getLogger("plant-cam.upload")


class GatewayUploader:
    def __init__(self, cfg: Config) -> None:
        self.url = f"{cfg.gateway.url}/api/v1/devices/{cfg.device.id}/frames"
        self.auth = (cfg.device.id, cfg.secret())
        self.timeout = cfg.gateway.timeout_s
        self.verify = cfg.gateway.verify_tls
        self.session = requests.Session()
        self.failures = 0

    def upload(self, jpeg: bytes, attempts: int = 3) -> str | None:
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.post(
                    self.url,
                    data=jpeg,
                    auth=self.auth,
                    timeout=self.timeout,
                    verify=self.verify,
                    headers={"Content-Type": "image/jpeg"},
                )
                if resp.status_code in (200, 201):
                    self.failures = 0
                    return resp.text.strip()
                if resp.status_code in (401, 403):
                    log.error(
                        "gateway rejected our credentials -- check device.secret",
                        extra={"fields": {"status": resp.status_code}},
                    )
                    break
                log.warning(
                    "snapshot upload failed", extra={"fields": {"status": resp.status_code, "body": resp.text[:200]}}
                )
            except requests.RequestException as exc:
                log.warning(
                    "snapshot upload attempt failed",
                    extra={"fields": {"attempt": attempt, "of": attempts, "error": repr(exc)}},
                )
            if attempt < attempts:
                time.sleep(min(2.0**attempt, 10.0))
        self.failures += 1
        return None
