"""Acquisition record types — :class:`DaqReading`, :class:`DaqBlock`.

:class:`DaqReading` is the cross-instrument scalar bridge that mirrors
``alicatlib.Sample`` and ``sartoriuslib.Sample`` — DAQ rows land in the
same SQLite/Parquet pipeline as flow-controller and balance rows,
joinable on ``(device, t_mono_ns)``.

:class:`DaqBlock` is the rectangular hardware-clocked record that carries
an ``np.ndarray`` of shape ``(n_channels, samples_per_channel)``. Per-sample
timestamps are reconstructed from ``t_mono_ns + k * block_period_ns``
(monotonic ns) or ``task_started_at`` + offset (wall clock).

Both records expose the cross-library §C timestamp contract:

- ``t_mono_ns: int`` — canonical join key (monotonic nanoseconds).
- ``t_utc: datetime`` — wall-clock acquisition instant, UTC, tz-aware.
- ``t_midpoint_mono_ns: int | None`` — integration-window midpoint.

For :class:`DaqReading` (software-timed polling), ``t_mono_ns`` / ``t_utc``
are the midpoint of the request/receive window. ``t_midpoint_mono_ns`` is
``None`` because the polled read has no separate integration window beyond
that I/O window.

For :class:`DaqBlock` (hardware-clocked), ``t_mono_ns`` / ``t_utc`` describe
sample index 0. ``t_midpoint_mono_ns`` is the midpoint of the full block
window (populated at construction).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    import numpy as np

    from nidaqlib.errors import ErrorContext, NIDaqError


class TaskState(StrEnum):
    """Coarse projection of NI DAQmx's documented task lifecycle.

    NI's underlying ``verified``/``reserved``/``committed`` states all
    bucket into :attr:`CONFIGURED` because the wrapper does not separately
    track them today. The other four are 1:1 with NI's documented states.
    """

    CREATED = "CREATED"
    """Task has been constructed; channels not yet applied."""

    CONFIGURED = "CONFIGURED"
    """Channels + timing applied (NI ``verified``/``reserved``/``committed``)."""

    RUNNING = "RUNNING"
    """``task.start()`` has been called."""

    STOPPED = "STOPPED"
    """Stopped but not yet closed; may transition back to RUNNING."""

    CLOSED = "CLOSED"
    """Backing NI task has been released."""


def _empty_metadata() -> dict[str, str | int | float | bool]:
    return {}


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqReading:
    """One scalar (or low-rate) reading across the channels of a task.

    Attributes:
        device: Manager-add name, or ``TaskSpec.name`` when emitted directly
            from a session. The cross-instrument join key.
        task: Underlying ``TaskSpec.name`` (optional second key).
        values: One entry per channel, keyed by channel display name.
        units: Engineering units, keyed by channel display name. ``None``
            entries indicate "no unit declared on the channel spec."
        t_mono_ns: ``time.monotonic_ns()`` at the midpoint of the
            request/receive window. Canonical join key.
        t_utc: Wall-clock at the midpoint of the request/receive window.
        t_midpoint_mono_ns: Optional integration-window midpoint. ``None``
            for software-timed polling — ``t_mono_ns`` already names the
            midpoint of the I/O window.
        requested_at: Wall-clock immediately before the read (provenance).
        received_at: Wall-clock immediately after the read returns
            (provenance).
        latency_s: ``received_at - requested_at`` in seconds.
        metadata: Free-form scalar metadata.
        error: Populated only under ``ErrorPolicy.RETURN``. Always ``None``
            under the default ``RAISE`` policy.
    """

    device: str
    task: str | None = None
    values: Mapping[str, float | int | bool]
    units: Mapping[str, str | None]
    t_mono_ns: int
    t_utc: datetime
    t_midpoint_mono_ns: int | None = None
    requested_at: datetime
    received_at: datetime
    latency_s: float
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)
    error: NIDaqError | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqBlock:
    """One rectangular block of hardware-clocked samples.

    The ``data`` field is the natural shape for Parquet row groups, NumPy
    slicing, and TDMS — do not scalarize unless the user opts in via
    :func:`nidaqlib.block_to_rows`.

    To recover the wall-clock or monotonic timestamp of sample ``k``::

        t_mono_k = block.t_mono_ns + k * block.block_period_ns
        elapsed_k = (block.first_sample_index + k) / block.sample_rate_hz
        t_wall_k = block.task_started_at + timedelta(seconds=elapsed_k)

    Do **not** interpolate sample times off ``read_started_at`` —
    that drifts block-to-block.

    Attributes:
        device: Manager-add name, or ``TaskSpec.name`` when emitted directly.
        task: Underlying ``TaskSpec.name``.
        channels: Channel display names in the row order of ``data``.
        data: NumPy array of shape ``(len(channels), samples_per_channel)``.
            ``dtype`` is ``float64`` for AI voltage.
        block_index: 0-based, monotonic per task. Resets on a new task.
        first_sample_index: Cumulative sample offset since ``task_started_at``.
        samples_per_channel: ``data.shape[1]``.
        block_period_ns: Integer nanoseconds between consecutive samples.
            ``None`` for on-demand reads (no clock).
        t_mono_ns: ``time.monotonic_ns()`` at sample index 0. Canonical join
            key.
        t_utc: Wall-clock at sample index 0 (UTC, tz-aware).
        t_midpoint_mono_ns: Midpoint of the full block window in
            ``monotonic_ns``; ``None`` for on-demand blocks.
        task_started_at: Wall-clock anchor for sample-time reconstruction.
        t0: Wall-clock at the first sample of *this* block; equals
            ``t_utc`` but kept as separate provenance.
        read_started_at: Wall-clock just before the read (provenance).
        read_finished_at: Wall-clock just after the read (provenance).
        elapsed_s: ``read_finished_at - read_started_at`` in seconds.
        units: Engineering units keyed by channel display name.
        error: Populated only under ``ErrorPolicy.RETURN``.
    """

    device: str
    task: str | None = None
    channels: tuple[str, ...]
    data: np.ndarray
    block_index: int
    first_sample_index: int
    samples_per_channel: int
    block_period_ns: int | None
    t_mono_ns: int
    t_utc: datetime
    t_midpoint_mono_ns: int | None
    task_started_at: datetime
    t0: datetime
    read_started_at: datetime
    read_finished_at: datetime
    elapsed_s: float
    units: Mapping[str, str | None]
    error: NIDaqError | None = None

    @property
    def sample_rate_hz(self) -> float | None:
        """Convenience: ``1e9 / block_period_ns``; ``None`` for on-demand."""
        if self.block_period_ns is None or self.block_period_ns == 0:
            return None
        return 1e9 / self.block_period_ns

    def __post_init__(self) -> None:
        """Validate the rectangular-shape invariant.

        Raises:
            NIDaqValidationError: ``data.shape`` does not equal
                ``(len(channels), samples_per_channel)``.
        """
        from nidaqlib.errors import NIDaqValidationError  # noqa: PLC0415

        n_channels = len(self.channels)
        expected = (n_channels, self.samples_per_channel)
        actual = tuple(self.data.shape)
        if actual != expected:
            raise NIDaqValidationError(
                f"DaqBlock data shape {actual} does not match (channels, "
                f"samples_per_channel) = {expected}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceSnapshot:
    """Cross-instrument snapshot of one device's state, captured without I/O.

    Shape-shared across sibling libraries (``alicatlib``, ``sartoriuslib``,
    ``watlowlib``). NI extras sit on :class:`NIDaqSnapshot`.

    Attributes:
        name: Device / task name.
        model: Hardware model string, when known.
        firmware: Firmware version, when known. Always ``None`` for NI.
        serial: Device serial number, when known.
        connected: ``True`` when the underlying resource is reachable.
        last_error: Most recent :class:`ErrorContext`, or ``None``.
        recoverable_error_count: Running count of swallowed
            :class:`NIDaqTransientError` events.
        captured_at: UTC, tz-aware wall-clock at snapshot construction.
    """

    name: str
    model: str | None
    firmware: str | None
    serial: str | None
    connected: bool
    last_error: ErrorContext | None
    recoverable_error_count: int
    captured_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class NIDaqSnapshot(DeviceSnapshot):
    """NI-specific snapshot — adds task state and physical inventory."""

    task_name: str
    task_state: TaskState
    channel_count: int
    timing_mode: str | None
    rate_hz: float | None
    physical_channels: tuple[str, ...]
    product_type: str | None
    chassis: str | None
    physical_module: str | None


__all__ = ["DaqBlock", "DaqReading", "DeviceSnapshot", "NIDaqSnapshot", "TaskState"]
