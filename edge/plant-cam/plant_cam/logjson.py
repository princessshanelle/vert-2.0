"""Structured logging to stdout (journald picks it up; nothing is written
to the SD card by this process).

    log.info("analysis done", extra={"fields": {"latency_ms": 812, "label": "steady"}})

renders as one JSON object per line:

    {"ts": "2026-10-12T09:00:00.123Z", "level": "INFO", "logger": "plant-cam", "msg": "analysis done",
     "latency_ms": 812, "label": "steady"}
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds")[:-6] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for k, v in fields.items():
                doc.setdefault(k, v)
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        out = super().format(record)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            out += "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        return out


def setup(level: str = "INFO", json_format: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_format else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # paho and urllib3 are chatty at DEBUG and say nothing we don't log ourselves.
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
