"""plant-cam command line.

python -m plant_cam run          --config /etc/plant-cam/config.yaml
python -m plant_cam check-config --config ...    validate config + profile + analyzer params, then exit
python -m plant_cam calibrate    --config ...    measure and lock white balance/exposure
python -m plant_cam benchmark    --config ... --images DIR [--iterations N] [--json out.json]
python -m plant_cam evaluate     --config ... --labels DIR   per-class precision/recall on labelled images
python -m plant_cam export-al    --config ... --out DIR      copy active-learning samples for labelling
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config as config_mod
from . import logjson
from .core.profile import ProfileError, load_profile
from .core.registry import PluginError, validate_params

log = logging.getLogger("plant-cam")


def _load(args: argparse.Namespace, require_secret: bool = True) -> tuple[config_mod.Config, object]:
    cfg = config_mod.load(args.config, require_secret=require_secret)
    logjson.setup(args.log_level or cfg.logging.level, cfg.logging.json_format and not args.text_logs)
    profile = load_profile(cfg.profile.id, Path(cfg.profile.dir) if cfg.profile.dir else None)
    from .core.pipeline import resolve_entries

    for e in resolve_entries(cfg, profile):
        validate_params("analyzer", e.name, e.params)
    for p in cfg.publishers:
        validate_params("publisher", p.name, p.params)
    return cfg, profile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="plant_cam", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "check-config", "calibrate", "benchmark", "evaluate", "export-al"],
    )
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--text-logs", action="store_true", help="human-readable logs instead of JSON")
    ap.add_argument("--images", help="benchmark: folder of images (default: the configured camera)")
    ap.add_argument("--iterations", type=int, default=10)
    ap.add_argument("--labels", help="evaluate: folder with images + ground-truth JSON")
    ap.add_argument("--json", help="benchmark/evaluate: also write results here")
    ap.add_argument("--out", help="export-al: destination folder")
    args = ap.parse_args(argv)

    try:
        cfg, profile = _load(args, require_secret=args.command == "run")
    except (config_mod.ConfigError, ProfileError, PluginError) as exc:
        logjson.setup("INFO", json_format=False)
        log.error("%s", exc)
        return 2

    from .core.profile import Profile

    assert isinstance(profile, Profile)
    if args.command == "check-config":
        print(f"config OK: device {cfg.device.id} in zone {cfg.device.zone}, profile {profile.id}@{profile.version}")
        return 0
    if args.command == "calibrate":
        from .capture import CaptureLoop, make_source

        loop = CaptureLoop(make_source(cfg.camera))
        loop.start()
        try:
            if not loop.wait_open(30):
                log.error("camera did not open")
                return 1
            print(json.dumps(loop.calibrate(settle_s=4.0), indent=2, default=str))
        finally:
            loop.stop()
        print("saved; restart plant-cam to use it (or send it the `calibrate` command next time)")
        return 0
    if args.command == "benchmark":
        from .bench import benchmark

        return benchmark(cfg, profile, args.images, args.iterations, args.json)
    if args.command == "evaluate":
        from .evaluate import evaluate_folder

        if not args.labels:
            ap.error("evaluate needs --labels DIR")
        return int(evaluate_folder(cfg, profile, Path(args.labels), args.json))
    if args.command == "export-al":
        if not args.out:
            ap.error("export-al needs --out DIR")
        from .store.active_learning import ActiveLearningStore

        n = ActiveLearningStore(cfg.active_learning, cfg.device.id).export(Path(args.out))
        print(f"exported {n} samples to {args.out}")
        return 0

    from .app import App

    return App(cfg, profile).run()


if __name__ == "__main__":
    sys.exit(main())
