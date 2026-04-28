"""Discovery model — :class:`DeviceInfo` (design doc §19.2)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DeviceInfo"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceInfo:
    """Snapshot of one NI device's identity and physical channel inventory.

    Populated by :func:`~nidaqlib.system.discovery.list_devices`. Frozen so
    consumers can pass it around without worrying about staleness — re-call
    discovery to refresh.
    """

    name: str
    product_type: str | None
    serial_number: str | None
    ai_physical_channels: tuple[str, ...]
    ao_physical_channels: tuple[str, ...]
    di_lines: tuple[str, ...]
    do_lines: tuple[str, ...]
    ci_physical_channels: tuple[str, ...]
    co_physical_channels: tuple[str, ...]
