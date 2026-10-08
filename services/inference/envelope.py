"""Shared message envelope — must stay in lockstep with
services/gateway/internal/mq/envelope.go. There is deliberately no shared
code between the Go and Python services; they agree on the JSON shape only.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclasses.dataclass
class Envelope:
    device_id: str
    device_type: str
    zone: str
    payload: dict[str, Any]
    schema_version: str = SCHEMA_VERSION
    seq: int = 0
    ts: str = dataclasses.field(default_factory=lambda: dt.datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
