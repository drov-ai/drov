"""Local file ownership, independent from SQLite's transaction locking."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import portalocker

from drov.errors import ConfigurationError, PersistenceUnavailable, StorageInUse

_REGISTRY: set[Path] = set()
_REGISTRY_LOCK = threading.Lock()


class FileOwnership:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.lock_path = Path(str(self.path) + ".drov.lock")
        self._lock: portalocker.Lock[Any] | None = None
        self._identity: tuple[int, int] | None = None

    def acquire(self) -> None:
        # portalocker probes the temporary directory during import. Load it only
        # when the application explicitly opens storage, never on SDK import.
        import portalocker

        if not self.path.parent.is_dir():
            raise ConfigurationError("Database parent directory does not exist")
        self._check_file()
        with _REGISTRY_LOCK:
            if self.path in _REGISTRY:
                raise StorageInUse("Database is already owned in this process")
            _REGISTRY.add(self.path)
        try:
            if self.lock_path.is_symlink():
                raise ConfigurationError("Database sidecar lock cannot be a symlink")
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            lock: portalocker.Lock[Any] = portalocker.Lock(
                self.lock_path,
                mode="a",
                timeout=0,
                flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            try:
                lock.acquire()
            except portalocker.exceptions.LockException as exc:
                raise StorageInUse("Database is owned by another process") from exc
            self._lock = lock
            if not self.path.exists():
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.close(descriptor)
                except FileExistsError:
                    pass
            self._check_file()
            info = self.path.stat()
            self._identity = (info.st_dev, info.st_ino)
        except BaseException:
            self.release()
            raise

    def _check_file(self) -> None:
        if self.path.exists():
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ConfigurationError("Database must be a regular local file without hard links")

    def check(self) -> None:
        try:
            info = self.path.stat()
        except OSError as exc:
            raise PersistenceUnavailable("Owned database is no longer available") from exc
        if (info.st_dev, info.st_ino) != self._identity or info.st_nlink != 1:
            raise PersistenceUnavailable("Owned database was replaced or hard-linked")

    def release(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        with _REGISTRY_LOCK:
            _REGISTRY.discard(self.path)
        self._identity = None
