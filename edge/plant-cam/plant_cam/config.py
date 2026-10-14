"""Typed configuration: YAML file, then environment overrides, validated
once at startup with messages a person can act on.

Environment overrides use PLANTCAM__<SECTION>__<KEY>, e.g.

    PLANTCAM__BROKER__HOST=192.168.1.151
    PLANTCAM__SCHEDULE__INTERVAL_S=300

Values are parsed as YAML scalars, so numbers and booleans work. The
device secret can also come from VERT_DEVICE_SECRET (a systemd
EnvironmentFile), which is how the deploy keeps it out of config.yaml.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from .core.profile import AnalyzerEntry
from .core.types import ROI

# A routing-key segment can't contain the MQTT/AMQP separators or
# wildcards -- the broker's per-device topic check splits on them.
_SEGMENT = re.compile(r"^[^./+#\s]+$")
_WINDOW = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceCfg(_Section):
    id: str
    secret: SecretStr = SecretStr("")
    type: str = "ai-camera"
    zone: str
    # Address announced for the dashboard's live view; null = detect it.
    advertise_ip: str | None = None

    @field_validator("id", "type", "zone")
    @classmethod
    def _segment(cls, v: str) -> str:
        if not _SEGMENT.match(v):
            raise ValueError(f"{v!r} must be non-empty with no '.', '/', '+', '#' or spaces")
        return v


class TlsCfg(_Section):
    enabled: bool = False
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None
    insecure: bool = False  # skip hostname verification (self-signed LAN CA without SANs)


class BrokerCfg(_Section):
    host: str
    port: int = Field(1883, ge=1, le=65535)
    keepalive_s: int = Field(30, ge=5)
    tls: TlsCfg = Field(default_factory=TlsCfg)
    backoff_min_s: float = 1.0
    backoff_max_s: float = 60.0


class GatewayCfg(_Section):
    url: str
    timeout_s: float = 15.0
    verify_tls: bool | str = True  # True, False, or a CA bundle path

    @field_validator("url")
    @classmethod
    def _strip(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v.rstrip("/")


class CameraCfg(_Section):
    source: Literal["picamera2", "folder", "video"] = "picamera2"
    # Main stream: what analyzers see. 1296x972 is the OV5647's binned
    # full-field-of-view mode; Camera Module 3 users may prefer 1536x864.
    main_size: tuple[int, int] = (1296, 972)
    # Lores stream: live view and change detection only. Must be smaller than main.
    lores_size: tuple[int, int] = (640, 480)
    # Full-sensor still for Stage B crops; null = crop from the main stream.
    still_size: tuple[int, int] | None = (2592, 1944)
    fps: float = Field(5.0, gt=0, le=30)
    hflip: bool = False
    vflip: bool = False
    # White balance and exposure: "locked" applies the values saved by
    # `plant_cam calibrate` (falls back to auto until calibrated).
    awb: Literal["auto", "locked"] = "locked"
    exposure: Literal["auto", "locked"] = "auto"
    calibration_file: str = "/var/lib/plant-cam/calibration.json"
    folder: str = "./samples"
    video: str = ""
    loop: bool = True
    stall_timeout_s: float = 45.0  # no frame AND no reopen attempt this long = wedged driver: exit for systemd


class ScheduleCfg(_Section):
    interval_s: float = Field(600.0, ge=5)
    # Local-time windows when analysis is allowed, e.g. ["07:00-11:00", "14:00-18:00"]
    # (grow lights off / daylight). Empty = any time.
    windows: list[str] = Field(default_factory=list)
    warmup_s: float = 5.0  # let auto-exposure settle after the camera opens
    change_detection: bool = True
    change_threshold: float = Field(0.08, gt=0, le=1)  # mean abs diff of a 64x48 grey thumbnail, 0-1
    change_min_interval_s: float = 120.0

    @field_validator("windows")
    @classmethod
    def _windows(cls, v: list[str]) -> list[str]:
        for w in v:
            m = _WINDOW.match(w)
            if not m or int(m[1]) > 23 or int(m[3]) > 24 or int(m[2]) > 59 or int(m[4]) > 59:
                raise ValueError(f"window {w!r} must look like 07:00-11:30")
        return v

    def in_window(self, now: dt.datetime) -> bool:
        if not self.windows:
            return True
        minutes = now.hour * 60 + now.minute
        for w in self.windows:
            m = _WINDOW.match(w)
            assert m
            start, end = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
            if (start <= minutes < end) if start <= end else (minutes >= start or minutes < end):
                return True
        return False


class RoiCfg(_Section):
    name: str
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 as 0-1 of the frame
    kinds: list[Literal["canopy", "fruit", "reference"]] = Field(default_factory=lambda: ["canopy", "fruit"])  # type: ignore[arg-type]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError("ROI names are lowercase letters, digits and _ (they become metric names)")
        return v

    @model_validator(mode="after")
    def _box(self) -> RoiCfg:
        x1, y1, x2, y2 = self.box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError(f"ROI {self.name}: box must be 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1")
        return self

    def to_roi(self) -> ROI:
        return ROI(self.name, self.box, frozenset(self.kinds))


class PreprocessCfg(_Section):
    # Name of a ROI (kinds: [reference]) covering a grey/white card; each
    # analysis rescales R,G,B so the card reads neutral.
    reference_roi: str | None = None
    max_gain: float = 2.5


class ProfileCfg(_Section):
    id: str = "strawberry"
    dir: str | None = None  # default: the profiles/ folder shipped with plant-cam


class PublisherEntry(_Section):
    name: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class EnvCfg(_Section):
    enabled: bool = True
    # Sensor payload fields to read from the zone's other devices.
    temp_fields: list[str] = Field(default_factory=lambda: ["temp_c"])
    rh_fields: list[str] = Field(default_factory=lambda: ["humidity_pct", "rh_pct"])
    wetness_fields: list[str] = Field(default_factory=lambda: ["leaf_wetness_pct", "leaf_wetness"])
    max_age_s: float = 900.0  # older readings are ignored
    sample_every_s: float = 300.0  # history resolution for the risk indices
    history_h: float = 48.0


class StreamCfg(_Section):
    enabled: bool = True
    port: int = 81  # the dashboard's live view expects http://<ip>:81/stream
    bind: str = "0.0.0.0"
    max_fps: float = 5.0
    jpeg_quality: int = Field(70, ge=10, le=95)
    overlay: bool = True
    always_on: bool = False
    idle_timeout_s: float = 300.0


class StoreCfg(_Section):
    dir: str = "/var/lib/plant-cam"
    outbox_max_rows: int = 2000
    outbox_max_age_h: float = 168.0  # 7 days


class ActiveLearningCfg(_Section):
    enabled: bool = True
    dir: str = "/var/lib/plant-cam/active-learning"
    max_bytes: int = 2 * 1024**3
    max_per_hour: int = 30
    random_rate: float = Field(0.02, ge=0, le=1)
    # Whole raw frames (no overlay) for Stage A labelling: one per this many
    # seconds, only when the light is good enough to judge. 0 = off.
    frame_every_s: float = Field(3600.0, ge=0)
    jpeg_quality: int = 92


class ThermalCfg(_Section):
    soft_c: float = 70.0
    hard_c: float = 75.0
    critical_c: float = 80.0
    hysteresis_c: float = 5.0
    temp_path: str = "/sys/class/thermal/thermal_zone0/temp"
    hwmon_glob: str = "/sys/class/hwmon/hwmon*"


class LoggingCfg(_Section):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_format: bool = Field(True, alias="json")


class Config(_Section):
    device: DeviceCfg
    broker: BrokerCfg
    gateway: GatewayCfg
    camera: CameraCfg = Field(default_factory=CameraCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)
    rois: list[RoiCfg] = Field(default_factory=list)
    preprocess: PreprocessCfg = Field(default_factory=PreprocessCfg)
    profile: ProfileCfg = Field(default_factory=ProfileCfg)
    # null = use the crop profile's default analyzer list.
    analyzers: list[AnalyzerEntry] | None = None
    # Order matters: snapshot runs before mqtt so the reading can carry image_ref.
    publishers: list[PublisherEntry] = Field(
        default_factory=lambda: [PublisherEntry(name="snapshot"), PublisherEntry(name="mqtt")]
    )
    # Model packages live in <models_dir>/<name>/<version>/ behind a `current`
    # symlink (deploy/deploy-model.sh); relative model_dir params resolve here.
    models_dir: str = "/opt/plant-cam/models"
    env: EnvCfg = Field(default_factory=EnvCfg)
    stream: StreamCfg = Field(default_factory=StreamCfg)
    store: StoreCfg = Field(default_factory=StoreCfg)
    active_learning: ActiveLearningCfg = Field(default_factory=ActiveLearningCfg)
    thermal: ThermalCfg = Field(default_factory=ThermalCfg)
    logging: LoggingCfg = Field(default_factory=LoggingCfg)
    heartbeat_s: float = Field(60.0, ge=5)

    @model_validator(mode="after")
    def _cross(self) -> Config:
        names = [r.name for r in self.rois]
        if len(names) != len(set(names)):
            raise ValueError("ROI names must be unique")
        ref = self.preprocess.reference_roi
        if ref and not any(r.name == ref and "reference" in r.kinds for r in self.rois):
            raise ValueError(f"preprocess.reference_roi {ref!r} must name a ROI with kinds: [reference]")
        lw, lh = self.camera.lores_size
        mw, mh = self.camera.main_size
        if lw > mw or lh > mh:
            raise ValueError("camera.lores_size must not be larger than camera.main_size")
        return self

    @property
    def topic_base(self) -> str:
        """MQTT form; RabbitMQ maps '/' to '.' for the AMQP consumers."""
        return f"vert/{self.device.zone}/{self.device.type}/{self.device.id}"

    def roi_list(self) -> list[ROI]:
        return [r.to_roi() for r in self.rois if "reference" not in r.kinds]

    def secret(self) -> str:
        return self.device.secret.get_secret_value()


class ConfigError(Exception):
    pass


ENV_PREFIX = "PLANTCAM__"


def _apply_env(raw: dict[str, Any], environ: dict[str, str]) -> None:
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        parts = [p.lower() for p in key[len(ENV_PREFIX) :].split("__") if p]
        if not parts:
            continue
        node = raw
        for p in parts[:-1]:
            child = node.get(p)
            if not isinstance(child, dict):
                child = {}
                node[p] = child
            node = child
        node[parts[-1]] = yaml.safe_load(value) if value else value
    secret = environ.get("VERT_DEVICE_SECRET")
    if secret:
        raw.setdefault("device", {})["secret"] = secret


def _format(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "(top level)"
        lines.append(f"  {where}: {err['msg']}")
    return "\n".join(lines)


def load(path: str | Path, environ: dict[str, str] | None = None, require_secret: bool = True) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"no config file at {path} -- copy config.example.yaml and fill it in")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping")
    _apply_env(raw, dict(os.environ) if environ is None else environ)
    try:
        cfg = Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}:\n{_format(exc)}") from exc
    if require_secret and not cfg.secret():
        raise ConfigError("device.secret is empty -- provision the device and set VERT_DEVICE_SECRET")
    return cfg
