"""ORION background agent: runs tasks without touching the mouse, keyboard,
focus or windows. See README.md for how to run it and wire it into ORION."""

from .engine import BackgroundAgent

__version__ = "0.1.0"
__all__ = ["BackgroundAgent", "__version__"]
