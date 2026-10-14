"""Shared runtime services handed to publishers (so a publisher plugin can
reach the broker link, the store or the gateway without importing the app)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config
    from ..link import MqttLink
    from ..publishers.uploader import GatewayUploader
    from ..store import Store
    from .types import ROI


@dataclasses.dataclass
class Services:
    cfg: Config
    store: Store
    link: MqttLink | None = None
    uploader: GatewayUploader | None = None
    rois: list[ROI] = dataclasses.field(default_factory=list)
    # Set by the app: True when the current analysis was requested by a person
    # (analyze_now), which the snapshot publisher treats as "always upload".
    on_demand: bool = False
