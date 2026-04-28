"""System discovery — :func:`list_devices`, :func:`list_physical_channels`.

Wraps :class:`nidaqmx.system.System.local()` enough to drive helpful CLI
commands and basic preflight validation; not a clone of NI MAX (design
doc §19).
"""

from __future__ import annotations

from nidaqlib.system.discovery import list_devices, list_physical_channels
from nidaqlib.system.models import DeviceInfo

__all__ = ["DeviceInfo", "list_devices", "list_physical_channels"]
