"""Single-owner storage backends; the transaction protocol is internal and unstable."""

from drov.storage.memory import MemoryStorage
from drov.storage.sqlite import SQLiteStorage

__all__ = ["MemoryStorage", "SQLiteStorage"]
