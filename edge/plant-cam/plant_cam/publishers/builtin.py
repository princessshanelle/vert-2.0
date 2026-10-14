"""Built-in publishers.

snapshot  annotated JPEG to the gateway's frame store (photo gallery);
          sets report.image_ref, so list it before `mqtt`
mqtt      the reading payload through the outbox (store-and-forward)
log       one structured log line per report (handy on a dev laptop)
"""

from __future__ import annotations

import logging
import time

from ..analyzers.classical import resize_max_edge
from ..core.registry import Publisher, PublisherParams, register_publisher
from ..core.types import ZoneReport
from .overlay import annotate, encode_jpeg, status_text
from .payload import build_payload

log = logging.getLogger("plant-cam.publish")


class MqttParams(PublisherParams):
    pass


@register_publisher("mqtt")
class MqttPublisher(Publisher):
    Params = MqttParams

    def publish(self, report: ZoneReport) -> None:
        link = self.services.link
        if link is None:
            log.warning("mqtt publisher has no broker link; reading dropped")
            return
        seq = link.enqueue_reading(build_payload(report), report.ts)
        log.debug("reading queued", extra={"fields": {"seq": seq, "connected": link.connected.is_set()}})


class SnapshotParams(PublisherParams):
    interval_s: float = 3600.0  # routine photo for the dashboard gallery
    on_alert: bool = True  # plus one whenever a condition becomes active
    on_request: bool = True  # and on analyze_now
    jpeg_quality: int = 85
    max_edge: int = 1296


@register_publisher("snapshot")
class SnapshotPublisher(Publisher):
    Params = SnapshotParams
    params: SnapshotParams

    def __init__(self, params: PublisherParams, services: object) -> None:
        super().__init__(params, services)  # type: ignore[arg-type]
        self._last = float("-inf")

    def publish(self, report: ZoneReport) -> None:
        p = self.params
        now = time.monotonic()
        due = now - self._last >= p.interval_s
        alert = p.on_alert and bool(report.newly_active)
        requested = p.on_request and self.services.on_demand
        if not (due or alert or requested) or self.services.uploader is None:
            return
        image = resize_max_edge(report.frame.image, p.max_edge)
        status = status_text(report.label, report.health_score, report.confidence)
        jpeg = encode_jpeg(annotate(image, report.detections, self.services.rois, status), p.jpeg_quality)
        ref = self.services.uploader.upload(jpeg)
        if ref:
            self._last = now
            report.image_ref = ref
            log.info("snapshot uploaded", extra={"fields": {"ref": ref, "alert": alert, "bytes": len(jpeg)}})


@register_publisher("log")
class LogPublisher(Publisher):
    def publish(self, report: ZoneReport) -> None:
        log.info("report", extra={"fields": {k: v for k, v in build_payload(report).items() if k != "conditions"}})
