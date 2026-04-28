"""System discovery — wraps ``nidaqmx.system.System.local()``.

Two helpers:

- :func:`list_devices` — returns one :class:`DeviceInfo` per visible
  device.
- :func:`list_physical_channels` — list AI / AO / counter channel names
  for one device.

The functions never raise on a clean system that has no NI hardware;
they return an empty list. Errors during the NI call are wrapped into
:class:`NIDaqBackendError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nidaqlib.errors import ErrorContext, NIDaqBackendError, NIDaqDependencyError
from nidaqlib.system.models import DeviceInfo

if TYPE_CHECKING:
    from typing import Any


__all__ = ["list_devices", "list_physical_channels"]


def _import_nidaqmx() -> Any:
    try:
        import nidaqmx  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise NIDaqDependencyError("nidaqmx-python is required for system discovery") from exc
    return nidaqmx


def _channel_names(collection: Any) -> tuple[str, ...]:
    """Pull `.name` off a NI channel collection, handling missing attributes."""
    try:
        return tuple(getattr(item, "name", str(item)) for item in collection)
    except Exception:  # pragma: no cover - defensive against odd NI shapes
        return ()


def list_devices() -> list[DeviceInfo]:
    """Enumerate visible NI devices and their physical-channel inventories.

    Returns:
        One :class:`DeviceInfo` per device. Empty list when no NI hardware
        is present.

    Raises:
        NIDaqBackendError: NI raised an unexpected error during enumeration.
        NIDaqDependencyError: ``nidaqmx-python`` is not installed.
    """
    nidaqmx = _import_nidaqmx()
    try:
        system = nidaqmx.system.System.local()
        devices = list(system.devices)
    except nidaqmx.errors.DaqError as exc:  # pragma: no cover - hardware path
        raise NIDaqBackendError(
            "failed to enumerate NI devices",
            context=ErrorContext(
                operation="list_devices",
                ni_error_code=getattr(exc, "error_code", None),
            ),
        ) from exc

    return [
        DeviceInfo(
            name=dev.name,
            product_type=getattr(dev, "product_type", None),
            serial_number=str(getattr(dev, "serial_num", None) or "") or None,
            ai_physical_channels=_channel_names(getattr(dev, "ai_physical_chans", ())),
            ao_physical_channels=_channel_names(getattr(dev, "ao_physical_chans", ())),
            di_lines=_channel_names(getattr(dev, "di_lines", ())),
            do_lines=_channel_names(getattr(dev, "do_lines", ())),
            ci_physical_channels=_channel_names(getattr(dev, "ci_physical_chans", ())),
            co_physical_channels=_channel_names(getattr(dev, "co_physical_chans", ())),
        )
        for dev in devices
    ]


def list_physical_channels(device: str) -> tuple[str, ...]:
    """Return AI physical channel names for ``device`` (e.g. ``"Dev1"``).

    For inventories of AO / DI / DO / counters, use :func:`list_devices` and
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
                operation="list_physical_channels",
                ni_error_code=getattr(exc, "error_code", None),
            ),
        ) from exc
