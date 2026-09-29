"""Object storage for the files students forward.

Infrastructure, beside ``db.py``: it knows how to put bytes somewhere and get
them back, nothing about what a resource is.

R2 speaks the S3 API, so this is boto3 against Cloudflare's endpoint. R2 is the
choice because egress is free -- a student retrieving a file is the product's
most common write-once-read-many action, and every other provider bills it.

``storage_ref`` is the **key**, never a URL. Signed URLs expire and endpoints get
migrated; a key stays true, and the row outlives both.
"""

import logging
import uuid
from functools import lru_cache
from mimetypes import guess_extension
from typing import Annotated, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends

from shelf.config import settings

logger = logging.getLogger(__name__)

STORAGE_TIMEOUT_SECONDS = 20


class StorageError(RuntimeError):
    """Put or get failed. The turn decides what to tell the student."""


def build_key(user_id: int, mime_type: str | None) -> str:
    """Per-student prefix, random name, best-effort extension.

    The student's filename is never the key: two people send ``notes.pdf`` on the
    same day and one would overwrite the other. The real name lives on
    ``resources.title`` where it belongs.
    """
    suffix = guess_extension(mime_type) if mime_type else None
    return f"u/{user_id}/{uuid.uuid4().hex}{suffix or ''}"


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """Store bytes. Returns the key, which is what goes in ``storage_ref``."""
        ...

    def get(self, key: str) -> bytes:
        """Fetch bytes back for a retrieval."""
        ...

    def delete(self, key: str) -> None:
        """Remove one object. Erasure is not erasure while the file survives.

        Deliberately idempotent: an object already gone is a success, because
        the caller is working from database rows and a partly-finished earlier
        erasure must be completable rather than permanently stuck.
        """
        ...


class R2Store:
    """Real Cloudflare R2 over the S3 API."""

    def __init__(self) -> None:
        self._bucket = settings.r2_bucket_name
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            # R2 ignores the region but boto3 insists on one being present.
            region_name="auto",
            config=Config(
                connect_timeout=STORAGE_TIMEOUT_SECONDS,
                read_timeout=STORAGE_TIMEOUT_SECONDS,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)
        except (BotoCoreError, ClientError) as error:
            raise StorageError(f"put failed for {key}") from error
        logger.info("stored object", extra={"storage_ref": key, "bytes": len(data)})
        return key

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
        except (BotoCoreError, ClientError) as error:
            raise StorageError(f"get failed for {key}") from error
        return body

    def delete(self, key: str) -> None:
        # S3 DELETE is already idempotent -- a missing key is a 204, not a 404.
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise StorageError(f"delete failed for {key}") from error
        logger.info("deleted object", extra={"storage_ref": key})


class MemoryStore:
    """Dev and test stand-in, so the file path runs with no R2 account.

    Process-local, like ``LogSender``: enough to prove the wiring, and obviously
    wrong to deploy.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        self.objects[key] = data
        logger.info("would store object", extra={"storage_ref": key, "bytes": len(data)})
        return key

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as error:
            raise StorageError(f"no object at {key}") from error

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@lru_cache(maxsize=1)
def _store() -> ObjectStore:
    """One client per process: boto3 clients are expensive to build and reusable."""
    if settings.r2_account_id and settings.r2_bucket_name:
        return R2Store()
    logger.warning("r2 not configured; files are stored in memory only")
    return MemoryStore()


def get_store() -> ObjectStore:
    return _store()


StoreDep = Annotated[ObjectStore, Depends(get_store)]
