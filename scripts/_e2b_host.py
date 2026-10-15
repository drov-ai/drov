"""Explicit host-only credential loading for opt-in E2B acceptance commands."""

import os
import stat
from pathlib import Path


def load_key(path: Path) -> str:
    """Parse one assignment; never execute a shell, export, log or upload the file."""
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size > 16384
        or (os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077))
    ):
        raise ValueError("Credential file must be a small private file owned by this user")
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().removeprefix("export ").partition("=")
        if separator and name.strip() == "E2B_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or not values[0] or any(char.isspace() for char in values[0]):
        raise ValueError("Credential file requires exactly one E2B_API_KEY assignment")
    return values[0]
