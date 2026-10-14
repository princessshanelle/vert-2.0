"""Plugin registry for analyzers and publishers.

Adding an analysis is one class plus one config entry:

    @register_analyzer("leaf_area")
    class LeafArea(Analyzer):
        class Params(AnalyzerParams):
            min_area_px: int = 50

        def analyze(self, frame, ctx):
            ...
            return AnalyzerResult(self.name, metrics={"leaf_area_px": ...})

    # config.yaml (or the crop profile)
    analyzers:
      - name: leaf_area
        params: {min_area_px: 80}

Plugins outside this package are found through the `plant_cam.analyzers`
and `plant_cam.publishers` entry-point groups, or by importing their
module from a crop profile's `plugins.py`.
"""

from __future__ import annotations

import abc
import logging
from collections.abc import Callable
from importlib import metadata
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from .types import AnalyzerResult, Context, Frame, ZoneReport

if TYPE_CHECKING:
    from .profile import Profile
    from .services import Services

log = logging.getLogger("plant-cam.registry")


class AnalyzerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublisherParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginError(Exception):
    pass


class Analyzer(abc.ABC):
    name: ClassVar[str] = ""
    Params: ClassVar[type[AnalyzerParams]] = AnalyzerParams
    # "heavy" analyzers (ML inference) are skipped first when the Pi runs hot.
    cost: ClassVar[Literal["light", "heavy"]] = "light"

    def __init__(self, params: AnalyzerParams, profile: Profile, instance_id: str | None = None) -> None:
        self.params = params
        self.profile = profile
        # Unique per pipeline entry (one class may run twice, e.g. leaf and fruit classifiers).
        self.id = instance_id or self.name

    @abc.abstractmethod
    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult: ...

    def close(self) -> None:  # noqa: B027 -- optional hook
        """Release models, threads, files."""


class Publisher(abc.ABC):
    name: ClassVar[str] = ""
    Params: ClassVar[type[PublisherParams]] = PublisherParams

    def __init__(self, params: PublisherParams, services: Services) -> None:
        self.params = params
        self.services = services

    def start(self) -> None:  # noqa: B027 -- optional hook
        pass

    @abc.abstractmethod
    def publish(self, report: ZoneReport) -> None:
        """Must not raise for transient failures (network down): buffer or
        log and return. Raising is reserved for bugs."""

    def stop(self) -> None:  # noqa: B027 -- optional hook
        pass


_ANALYZERS: dict[str, type[Analyzer]] = {}
_PUBLISHERS: dict[str, type[Publisher]] = {}
_entry_points_loaded = False

A = TypeVar("A", bound=type[Analyzer])
P = TypeVar("P", bound=type[Publisher])


def register_analyzer(name: str) -> Callable[[A], A]:
    def deco(cls: A) -> A:
        if name in _ANALYZERS and _ANALYZERS[name] is not cls:
            raise PluginError(f"analyzer {name!r} is registered twice ({_ANALYZERS[name]} and {cls})")
        cls.name = name
        _ANALYZERS[name] = cls
        return cls

    return deco


def register_publisher(name: str) -> Callable[[P], P]:
    def deco(cls: P) -> P:
        if name in _PUBLISHERS and _PUBLISHERS[name] is not cls:
            raise PluginError(f"publisher {name!r} is registered twice ({_PUBLISHERS[name]} and {cls})")
        cls.name = name
        _PUBLISHERS[name] = cls
        return cls

    return deco


def _load_entry_points() -> None:
    global _entry_points_loaded  # noqa: PLW0603 -- load-once guard
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    for group in ("plant_cam.analyzers", "plant_cam.publishers"):
        for ep in metadata.entry_points(group=group):
            try:
                ep.load()  # importing the class runs its @register_* decorator
            except Exception:
                log.exception("could not load plugin %s from entry point %s", ep.name, ep.value)


def _load_builtins() -> None:
    # Imported for their @register_* side effects.
    from .. import analyzers, publishers  # noqa: F401


def analyzer_names() -> list[str]:
    _load_builtins()
    _load_entry_points()
    return sorted(_ANALYZERS)


def publisher_names() -> list[str]:
    _load_builtins()
    _load_entry_points()
    return sorted(_PUBLISHERS)


def _params(cls: type[Analyzer] | type[Publisher], raw: dict[str, Any], what: str) -> Any:
    try:
        return cls.Params.model_validate(raw)
    except ValidationError as exc:
        raise PluginError(f"bad params for {what} {cls.name!r}:\n{exc}") from exc


def create_analyzer(
    name: str, raw_params: dict[str, Any], profile: Profile, instance_id: str | None = None
) -> Analyzer:
    _load_builtins()
    _load_entry_points()
    cls = _ANALYZERS.get(name)
    if cls is None:
        raise PluginError(f"unknown analyzer {name!r}; available: {', '.join(sorted(_ANALYZERS))}")
    return cls(_params(cls, raw_params, "analyzer"), profile, instance_id)


def create_publisher(name: str, raw_params: dict[str, Any], services: Services) -> Publisher:
    _load_builtins()
    _load_entry_points()
    cls = _PUBLISHERS.get(name)
    if cls is None:
        raise PluginError(f"unknown publisher {name!r}; available: {', '.join(sorted(_PUBLISHERS))}")
    return cls(_params(cls, raw_params, "publisher"), services)


def validate_params(kind: Literal["analyzer", "publisher"], name: str, raw: dict[str, Any]) -> None:
    """Startup validation, so a typo in config fails before the camera opens."""
    _load_builtins()
    _load_entry_points()
    table: dict[str, Any] = _ANALYZERS if kind == "analyzer" else _PUBLISHERS
    cls = table.get(name)
    if cls is None:
        raise PluginError(f"unknown {kind} {name!r}; available: {', '.join(sorted(table))}")
    _params(cls, raw, kind)
