"""System discovery — :func:`find_devices`, :func:`list_physical_channels`.

See :mod:`nidaqlib.system.discovery` for behaviour and the
:class:`DiscoveryResult` / :class:`DeviceInfo` shapes.
"""

from __future__ import annotations

from nidaqlib.system.discovery import find_devices, list_physical_channels
from nidaqlib.system.models import DeviceInfo, DiscoveryResult, NIDaqDiscoveryResult

__all__ = [
    "DeviceInfo",
    "DiscoveryResult",
    "NIDaqDiscoveryResult",
    "find_devices",
    "list_physical_channels",
]
