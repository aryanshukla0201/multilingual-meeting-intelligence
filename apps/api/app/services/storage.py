from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

from app.settings import settings


class StorageError(RuntimeError):
    pass


class StorageProvider(ABC):
    @abstractmethod
    def put_file(self, source_path: Path, storage_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, storage_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def materialize(self, storage_key: str, destination: Path) -> None:
        raise NotImplementedError


class LocalStorageProvider(StorageProvider):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, storage_key: str) -> Path:
        normalized = storage_key.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise StorageError("Invalid storage key")
        destination = (self.root / Path(*path.parts)).resolve()
        try:
            destination.relative_to(self.root)
        except ValueError as exc:
            raise StorageError("Invalid storage key") from exc
        return destination

    def put_file(self, source_path: Path, storage_key: str) -> None:
        destination = self._safe_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source_path.open("rb") as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target, length=settings.upload_chunk_size)

    def delete(self, storage_key: str) -> None:
        path = self._safe_path(storage_key)
        if path.exists():
            path.unlink()

    def exists(self, storage_key: str) -> bool:
        return self._safe_path(storage_key).is_file()

    def materialize(self, storage_key: str, destination: Path) -> None:
        source = self._safe_path(storage_key)
        if not source.is_file():
            raise StorageError("Storage object not found")
        with source.open("rb") as source_file, destination.open("wb") as destination_file:
            shutil.copyfileobj(source_file, destination_file, length=settings.upload_chunk_size)


class S3StorageProvider(StorageProvider):
    def __init__(
        self,
        bucket: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        if not bucket:
            raise StorageError("S3 bucket is required")
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    @staticmethod
    def _validate_key(storage_key: str) -> str:
        normalized = storage_key.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise StorageError("Invalid storage key")
        return "/".join(path.parts)

    def put_file(self, source_path: Path, storage_key: str) -> None:
        self.client.upload_file(str(source_path), self.bucket, self._validate_key(storage_key))

    def delete(self, storage_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._validate_key(storage_key))

    def exists(self, storage_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._validate_key(storage_key))
        except self.client.exceptions.ClientError:
            return False
        return True

    def materialize(self, storage_key: str, destination: Path) -> None:
        self.client.download_file(self.bucket, self._validate_key(storage_key), str(destination))


def create_storage_provider() -> StorageProvider:
    if settings.storage_provider.lower() == "s3":
        return S3StorageProvider(
            bucket=settings.storage_bucket or "",
            region=settings.storage_region,
            endpoint_url=settings.storage_endpoint_url,
            access_key_id=settings.storage_access_key_id,
            secret_access_key=settings.storage_secret_access_key,
        )
    if settings.storage_provider.lower() == "local":
        return LocalStorageProvider(settings.storage_path)
    raise StorageError(f"Unsupported storage provider: {settings.storage_provider}")
