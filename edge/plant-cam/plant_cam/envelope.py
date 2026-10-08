"""Shared message envelope -- must stay in lockstep with
services/gateway/internal/mq/envelope.go (and its other mirror,
services/inference/envelope.py). No shared code, only a shared JSON shape.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclasses.dataclass
class Envelope:
    device_id: str
    device_type: str
    zone: str
    payload: dict[str, Any]
    schema_version: str = SCHEMA_VERSION
    seq: int = 0
    # Stamped when the reading is taken, not when it's sent, so a reading
    # replayed from the offline spool lands at the right point on charts.
    ts: str = dataclasses.field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
