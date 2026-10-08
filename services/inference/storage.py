"""MinIO/S3 access for the inference service.

This service only ever reads -- it fetches the one frame it was told
about and never writes anything back to the bucket. It currently reuses
the same MinIO admin credentials the gateway uses (see docker-compose.yml);
scoping this down to a read-only, bucket-restricted credential is tracked
in the project overview's gaps list and is a good first "harden this"
task for whoever picks it up next.
"""
from __future__ import annotations

import logging

from minio import Minio
from minio.error import S3Error

from config import Config

log = logging.getLogger("vert-inference")


class FrameStore:
    def __init__(self, cfg: Config):
        self._client = Minio(
            cfg.s3_endpoint,
            access_key=cfg.s3_access_key,
            secret_key=cfg.s3_secret_key,
            secure=cfg.s3_use_ssl,
        )
        self._bucket = cfg.s3_bucket

    def fetch(self, object_key: str) -> bytes | None:
        """Returns the raw image bytes, or None if the object can't be
        read -- a missing or corrupted frame should never crash the
        service, just skip that one photo and move on to the next.
        """
        response = None
        try:
            response = self._client.get_object(self._bucket, object_key)
            return response.read()
        except S3Error as exc:
            log.warning("could not fetch frame %s from bucket %s: %s", object_key, self._bucket, exc)
            return None
        finally:
            if response is not None:
                response.close()
                response.release_conn()
