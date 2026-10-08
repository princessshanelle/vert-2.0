"""Loads config.yaml into typed sections, with defaults for everything
except the device's identity and where the VERT server lives.

The device secret may also come from the VERT_DEVICE_SECRET environment
variable (e.g. a systemd EnvironmentFile), which wins over the file.
"""
from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

import yaml


@dataclasses.dataclass
class DeviceCfg:
    id: str
    secret: str = ""
    type: str = "ai-camera"
    zone: str = "zoneA"  # must match a zone's `name` in the dashboard


@dataclasses.dataclass
class BrokerCfg:
    host: str
    port: int = 1883
    keepalive_s: int = 30


@dataclasses.dataclass
class GatewayCfg:
    url: str  # e.g. http://192.168.1.25:8080
    timeout_s: float = 15.0


@dataclasses.dataclass
class CameraCfg:
    source: str = "picamera2"  # picamera2 | opencv | folder
    width: int = 1280
    height: int = 720
    fps: int = 10
    hflip: bool = False
    vflip: bool = False
    opencv_index: int = 0
    folder: str = "./samples"
    # No new frame for this long means the camera is wedged: exit and let
    # systemd restart the whole process rather than limp along blind.
    stall_timeout_s: float = 30.0


@dataclasses.dataclass
class ModelCfg:
    dir: str = "./models/current"  # holds model.tflite + meta.json
    score_threshold: float = 0.35
    iou_threshold: float = 0.45
    # One core left free so the live stream stays smooth during inference.
    num_threads: int = 3


@dataclasses.dataclass
class AnalysisCfg:
    interval_s: float = 30.0
    window: int = 5  # analyses smoothed into each published reading


@dataclasses.dataclass
class EvidenceCfg:
    # Routine annotated photo to the gateway, so the dashboard gallery
    # has something recent even when nothing is wrong.
    interval_s: float = 900.0
    # Also upload immediately when the plant gets worse.
    on_decline: bool = True


@dataclasses.dataclass
class StreamCfg:
    port: int = 81  # the dashboard's live view expects http://<ip>:81/stream
    max_fps: float = 10.0
    jpeg_quality: int = 75
    overlay: bool = True
    always_on: bool = False
    idle_timeout_s: float = 300.0


@dataclasses.dataclass
class DatasetCfg:
    # Raw (unannotated) photos saved locally to label later -- the best
    # possible training data is this exact camera, at this exact angle.
    enabled: bool = True
    dir: str = "/var/lib/plant-cam/dataset"
    interval_s: float = 600.0
    max_files: int = 5000


@dataclasses.dataclass
class SpoolCfg:
    path: str = "/var/lib/plant-cam/spool.jsonl"
    max_entries: int = 5000


@dataclasses.dataclass
class Config:
    device: DeviceCfg
    broker: BrokerCfg
    gateway: GatewayCfg
    camera: CameraCfg
    model: ModelCfg
    analysis: AnalysisCfg
    evidence: EvidenceCfg
    stream: StreamCfg
    dataset: DatasetCfg
    spool: SpoolCfg

    @property
    def topic_base(self) -> str:
        return f"vert/{self.device.zone}/{self.device.type}/{self.device.id}"


class ConfigError(Exception):
    pass


# A routing-key segment can't contain the MQTT/AMQP separators or
# wildcards -- the broker's per-device topic check splits on them.
_SEGMENT_FORBIDDEN = re.compile(r"[./+#]")


def _section(cls, raw: dict, key: str, required: bool = False):
    data = raw.get(key)
    if data is None:
        if required:
            raise ConfigError(f"config is missing the required `{key}:` section")
        data = {}
    try:
        return cls(**data)
    except TypeError as exc:
        raise ConfigError(f"bad `{key}:` section: {exc}") from exc


def load(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"no config file at {path} -- copy config.example.yaml and fill it in")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cfg = Config(
        device=_section(DeviceCfg, raw, "device", required=True),
        broker=_section(BrokerCfg, raw, "broker", required=True),
        gateway=_section(GatewayCfg, raw, "gateway", required=True),
        camera=_section(CameraCfg, raw, "camera"),
        model=_section(ModelCfg, raw, "model"),
        analysis=_section(AnalysisCfg, raw, "analysis"),
        evidence=_section(EvidenceCfg, raw, "evidence"),
        stream=_section(StreamCfg, raw, "stream"),
        dataset=_section(DatasetCfg, raw, "dataset"),
        spool=_section(SpoolCfg, raw, "spool"),
    )

    cfg.device.secret = os.environ.get("VERT_DEVICE_SECRET", cfg.device.secret)
    if not cfg.device.secret:
        raise ConfigError("device.secret is empty -- provision the device and paste the secret it was issued")
    for name, value in (("device.id", cfg.device.id), ("device.type", cfg.device.type), ("device.zone", cfg.device.zone)):
        if not value or _SEGMENT_FORBIDDEN.search(value):
            raise ConfigError(f"{name} must be non-empty and contain no '.', '/', '+' or '#': {value!r}")
    if cfg.camera.source not in ("picamera2", "opencv", "folder"):
        raise ConfigError(f"camera.source must be picamera2, opencv or folder, not {cfg.camera.source!r}")
    cfg.gateway.url = cfg.gateway.url.rstrip("/")
    return cfg
