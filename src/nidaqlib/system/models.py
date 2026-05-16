"""System-discovery models — :class:`DeviceInfo`, :class:`DiscoveryResult`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nidaqlib.errors import NIDaqError, ProtocolKind


__all__ = ["DeviceInfo", "DiscoveryResult", "NIDaqDiscoveryResult"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceInfo:
    """Snapshot of one NI device's identity and physical-channel inventory.

    Cached on a :class:`~nidaqlib.tasks.session.DaqSession` at configure
    time and surfaced by :meth:`DaqSession.snapshot`. The
    :func:`~nidaqlib.system.discovery.find_devices` enumeration also
    wraps a populated :class:`DeviceInfo` inside each successful
    :class:`DiscoveryResult`.
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


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveryResult:
    """One enumeration row from :func:`find_devices`.

    Shape-shared across sibling libraries (``alicatlib``,
    ``sartoriuslib``, ``watlowlib``) so cross-instrument tooling can join
    on a common discovery record.

    Attributes:
        ok: ``True`` when the row describes a healthy device. ``False`` is
            reserved for enumeration-level driver failures, which surface
            as a single ``ok=False`` row carrying ``error``.
        port: NI device name (``Dev1``, ``cDAQ1Mod3``). Empty string only
            when ``ok=False`` and the driver call failed before any name
            could be read.
        address: Always ``None`` for NI (no multi-drop address concept).
        baudrate: Always ``None`` for NI (no serial line).
        protocol: Always ``None`` for NI (no wire protocol).
        device_info: Populated only when ``ok=True``. ``None`` on error
            rows.
        error: Populated only when ``ok=False``. ``None`` on healthy rows.
        elapsed_s: Wall-clock seconds spent enumerating this entry.
    """

    ok: bool
    port: str
    address: str | int | None = None
    baudrate: int | None = None
    protocol: ProtocolKind | None = None
    device_info: DeviceInfo | None = None
    error: NIDaqError | None = None
    elapsed_s: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class NIDaqDiscoveryResult(DiscoveryResult):
    """NI-specific discovery row carrying product / chassis identity.

    Subclasses :class:`DiscoveryResult` without renaming any base fields.
    The NI extras (``product_type``, ``serial_number``, ``chassis``,
    ``physical_module``) sit alongside the shape-shared base.
    """

    product_type: str | None = None
    serial_number: str | None = None
    chassis: str | None = None
    physical_module: str | None = None
