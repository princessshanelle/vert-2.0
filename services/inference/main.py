"""VERT inference service.

Watches for camera-frame-ready events, looks at the photo (see vision.py
for exactly what "looks at" means today -- an honest heuristic, not a
trained model yet), and republishes the result as an ordinary telemetry
reading. The automation engine and dashboard don't need to know AI
produced it rather than a sensor -- to them it's just another reading
from a device, on the same envelope, through the same pipe.
"""
from __future__ import annotations

import logging
import signal
import sys

from config import Config
from consumer import InferenceConsumer
from storage import FrameStore

log = logging.getLogger("vert-inference")


def main() -> None:
    cfg = Config.from_env()
    logging.basicConfig(level=cfg.log_level, format="[%(levelname)s] %(asctime)s %(message)s")

    log.info("starting up -- bucket=%s endpoint=%s", cfg.s3_bucket, cfg.s3_endpoint)

    frames = FrameStore(cfg)
    consumer = InferenceConsumer(cfg, frames)

    def handle_shutdown(signum, _frame):
        log.info("received signal %s, shutting down gracefully", signum)
        consumer.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    consumer.start()


if __name__ == "__main__":
    main()
