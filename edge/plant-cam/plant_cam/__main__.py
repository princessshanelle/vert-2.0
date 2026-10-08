"""Entry point: python -m plant_cam --config /etc/plant-cam/config.yaml"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config as config_mod
from .app import App


def main() -> int:
    p = argparse.ArgumentParser(prog="plant_cam", description="VERT on-device plant-health camera")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        logging.error("%s", exc)
        return 2
    return App(cfg).run()


if __name__ == "__main__":
    sys.exit(main())
