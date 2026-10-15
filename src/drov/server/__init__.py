"""Optional HTTP entry point; importing this module never imports the Web stack."""

from drov.server.app import DrovApp
from drov.server.types import ServerLimits

__all__ = ["DrovApp", "ServerLimits"]
