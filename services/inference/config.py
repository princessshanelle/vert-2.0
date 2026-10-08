"""Configuration for the inference service, read once at startup.

Kept as one small module (rather than scattering os.environ.get calls
through the codebase) so every setting this service depends on is
visible in one place -- useful the day someone has to figure out why a
container behaves differently from their laptop.
"""
from __future__ import annotations

import dataclasses
import os


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


@dataclasses.dataclass(frozen=True)
class Config:
    amqp_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_use_ssl: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            amqp_url=os.environ.get("VERT_AMQP_URL", "amqp://vert_inference:vert_inference_dev_password@localhost:5672/"),
            s3_endpoint=os.environ.get("VERT_S3_ENDPOINT", "localhost:9000"),
            s3_access_key=os.environ.get("VERT_S3_ACCESS_KEY", "vert_minio_admin"),
            s3_secret_key=os.environ.get("VERT_S3_SECRET_KEY", "vert_minio_dev_password"),
            s3_bucket=os.environ.get("VERT_S3_BUCKET", "vert-frames"),
            s3_use_ssl=_bool("VERT_S3_USE_SSL", "false"),
            log_level=os.environ.get("VERT_LOG_LEVEL", "INFO"),
        )
