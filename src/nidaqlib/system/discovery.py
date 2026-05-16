"""System discovery — wraps ``nidaqmx.system.System.local()``.

Two helpers:

- :func:`find_devices` — returns one :class:`DiscoveryResult` per visible
  device, with a populated :class:`DeviceInfo` on success rows.
- :func:`list_physical_channels` — list AI physical channel names for one
  device.

:func:`find_devices` **never raises**. A clean system with no NI hardware
returns an empty list; an enumeration-level driver failure surfaces as a
single ``ok=False`` row.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from nidaqlib.errors import ErrorContext, NIDaqBackendError, NIDaqDependencyError, NIDaqError
from nidaqlib.system.models import DeviceInfo, DiscoveryResult, NIDaqDiscoveryResult

if TYPE_CHECKING:
    from typing import Any


__all__ = ["find_devices", "list_physical_channels"]


def _import_nidaqmx() -> Any:
    try:
        import nidaqmx  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise NIDaqDependencyError("nidaqmx-python is required for system discovery") from exc
    return nidaqmx


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Return an NI object's attribute when supported, otherwise ``default``."""
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _channel_names(collection: Any) -> tuple[str, ...]:
    """Pull `.name` off a NI channel collection, handling missing attributes."""
    try:
        return tuple(str(_safe_attr(item, "name", str(item))) for item in collection)
    except Exception:  # pragma: no cover - defensive against odd NI shapes
        return ()


def _chassis_of(dev: Any) -> str | None:
    """Best-effort chassis name for a cDAQ module, ``None`` for anything else.

    NI raises ``-200197`` ("Device does not support this property") when
    asked for ``compact_daq_chassis_device`` on a non-module device. We
    swallow the error and return ``None`` — discovery must not raise.
    """
    chassis = _safe_attr(dev, "compact_daq_chassis_device")
    if chassis is None:
        return None
    name = _safe_attr(chassis, "name")
    return str(name) if name is not None else None


def _physical_module_of(dev: Any) -> str | None:
    """Best-effort module slot identifier; same string as ``name`` for cDAQ mods."""
    name = _safe_attr(dev, "name")
    return str(name) if name is not None else None


def find_devices() -> list[DiscoveryResult]:
    """Enumerate NI DAQ devices visible to the driver. Never raises.

    Returns:
        One :class:`NIDaqDiscoveryResult` per device. Empty list when no
        NI hardware is present. On enumeration-level failure (driver
        missing, system call raises), returns a single ``ok=False`` row
        with ``error`` populated.
    """
    try:
        nidaqmx = _import_nidaqmx()
    except NIDaqDependencyError as exc:
        return [
            DiscoveryResult(
                ok=False,
                port="",
                error=exc,
                elapsed_s=0.0,
            )
        ]

    started = time.monotonic()
    try:
        system = nidaqmx.system.System.local()
        devices = list(system.devices)
    except (nidaqmx.errors.DaqNotFoundError, nidaqmx.errors.DaqNotSupportedError) as exc:
        # Driver not installed, or platform unsupported (e.g. darwin). Neither
        # subclasses DaqError, so they need their own clause — surface as a
        # dependency failure rather than a backend error.
        elapsed = time.monotonic() - started
        wrapped: NIDaqError = NIDaqDependencyError(str(exc))
        wrapped.__cause__ = exc
        return [
            DiscoveryResult(
                ok=False,
                port="",
                error=wrapped,
                elapsed_s=elapsed,
            )
        ]
    except nidaqmx.errors.DaqError as exc:  # pragma: no cover — hardware path
        elapsed = time.monotonic() - started
        wrapped = NIDaqBackendError(
            "failed to enumerate NI devices",
            context=ErrorContext(
                command_name="find_devices",
                ni_error_code=getattr(exc, "error_code", None),
            ),
        )
        wrapped.__cause__ = exc
        return [
            DiscoveryResult(
                ok=False,
                port="",
                error=wrapped,
                elapsed_s=elapsed,
            )
        ]

    results: list[DiscoveryResult] = []
    for dev in devices:
        per_started = time.monotonic()
        name = str(_safe_attr(dev, "name", ""))
        product_type = _safe_attr(dev, "product_type")
        serial_num = _safe_attr(dev, "serial_num")
        info = DeviceInfo(
            name=name,
            product_type=str(product_type) if product_type is not None else None,
            serial_number=str(serial_num or "") or None,
            ai_physical_channels=_channel_names(_safe_attr(dev, "ai_physical_chans", ())),
            ao_physical_channels=_channel_names(_safe_attr(dev, "ao_physical_chans", ())),
            di_lines=_channel_names(_safe_attr(dev, "di_lines", ())),
            do_lines=_channel_names(_safe_attr(dev, "do_lines", ())),
            ci_physical_channels=_channel_names(_safe_attr(dev, "ci_physical_chans", ())),
            co_physical_channels=_channel_names(_safe_attr(dev, "co_physical_chans", ())),
        )
        results.append(
            NIDaqDiscoveryResult(
                ok=True,
                port=name,
                device_info=info,
                product_type=info.product_type,
                serial_number=info.serial_number,
                chassis=_chassis_of(dev),
                physical_module=_physical_module_of(dev),
                elapsed_s=time.monotonic() - per_started,
            )
        )
    return results


def list_physical_channels(device: str) -> tuple[str, ...]:
    """Return AI physical channel names for ``device`` (e.g. ``"Dev1"``).

    For AO / DI / DO / counter inventories, use :func:`find_devices` and
    inspect the returned :class:`DeviceInfo`.

    Raises:
        NIDaqBackendError: NI rejected the request (e.g. unknown device).
    """
    nidaqmx = _import_nidaqmx()
    try:
        dev = nidaqmx.system.Device(device)
        return _channel_names(dev.ai_physical_chans)
    except nidaqmx.errors.DaqError as exc:  # pragma: no cover - hardware path
        raise NIDaqBackendError(
            f"failed to list physical channels for {device!r}",
            context=ErrorContext(
                command_name="list_physical_channels",
                ni_error_code=getattr(exc, "error_code", None),
            ),
        ) from exc
