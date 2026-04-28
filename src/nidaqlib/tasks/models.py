"""Acquisition record types — :class:`DaqReading`, :class:`DaqBlock`, :class:`DaqSample`.

:class:`DaqReading` is the cross-instrument scalar bridge that mirrors
``alicatlib.Sample`` and ``sartoriuslib.Sample`` (design doc §8.6 / §8.8) —
DAQ rows land in the same SQLite/Parquet pipeline as flow-controller and
balance rows, joinable on ``(device, monotonic_ns)``.

:class:`DaqBlock` is the rectangular hardware-clocked record that carries an
``np.ndarray`` of shape ``(n_channels, samples_per_channel)`` (design doc §8.7).
Sample timestamps are reconstructed from ``task_started_at`` plus
``first_sample_index`` — the wall-clock provenance fields are not per-sample
truth and must not be interpolated against.

:class:`DaqSample` is the per-channel-per-sample scalarized row produced by
``block_to_long_rows()`` for opt-in CSV/JSONL fan-out (design doc §8.9). It
is never produced automatically by recorders or sinks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    import numpy as np

    from nidaqlib.errors import NIDaqError


def _empty_metadata() -> dict[str, str | int | float | bool]:
    return {}


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqReading:
    """One scalar (or low-rate) reading across the channels of a task.

    Field shape mirrors ``alicatlib.Sample`` and ``sartoriuslib.Sample`` so
    that DAQ rows join cleanly against flow-controller and balance rows on
    ``(device, monotonic_ns)``. See design doc §8.6 / §8.8.

    Attributes:
        device: Manager-add name, or ``TaskSpec.name`` when emitted directly
            from a session. This is the cross-instrument join key.
        task: Underlying ``TaskSpec.name`` (optional second key).
        values: One entry per channel, keyed by channel display name.
        units: Engineering units, keyed by channel display name. ``None``
            entries indicate "no unit declared on the channel spec."
        requested_at: Wall-clock immediately before the read.
        received_at: Wall-clock immediately after the read returns.
        midpoint_at: Midpoint of the request/receive window.
        monotonic_ns: ``time.monotonic_ns()`` at the midpoint. Use this — not
            wall-clock — for join arithmetic; wall-clock is non-monotonic
            across clock adjustments.
        elapsed_s: ``received_at - requested_at`` in seconds.
        metadata: Free-form scalar metadata (often the source ``TaskSpec``'s
            metadata, optionally merged with manager-level metadata).
        error: Populated only under ``ErrorPolicy.RETURN``. Always ``None``
            under the default ``RAISE`` policy.
    """

    device: str
    task: str | None = None
    values: Mapping[str, float | int | bool]
    units: Mapping[str, str | None]
    requested_at: datetime
    received_at: datetime
    midpoint_at: datetime
    monotonic_ns: int
    elapsed_s: float
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)
    error: NIDaqError | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqBlock:
    """One rectangular block of hardware-clocked samples.

    The ``data`` field is the natural shape for Parquet row groups, NumPy
    slicing, and TDMS — *do not scalarize unless the user opts in via
    ``block_to_long_rows()``*.

    To recover the wall-clock timestamp of sample ``k`` (where
    ``0 <= k < samples_per_channel``)::

        absolute = block.first_sample_index + k
        elapsed = absolute / block.sample_rate_hz
        sample_at = block.task_started_at + timedelta(seconds=elapsed)

    Do **not** interpolate sample times off ``t0`` or ``read_started_at`` —
    those drift block-to-block.

    Attributes:
        device: Manager-add name, or ``TaskSpec.name`` when emitted directly.
        task: Underlying ``TaskSpec.name``.
        channels: Channel display names in the row order of ``data``.
        data: NumPy array. Invariant — shape is
            ``(len(channels), samples_per_channel)`` and is asserted in
            :meth:`__post_init__`. ``dtype`` is ``float64`` for AI voltage.
        block_index: 0-based, monotonic per task. Resets on a new task.
        first_sample_index: Cumulative sample offset since ``task_started_at``.
        samples_per_channel: ``data.shape[1]``. Held redundantly so consumers
            need not import NumPy to inspect block size.
        sample_rate_hz: From ``Timing.rate_hz``. ``None`` for on-demand reads.
        dt_s: ``1 / sample_rate_hz`` when ``sample_rate_hz`` is set.
        task_started_at: Wall-clock anchor for sample-time reconstruction.
        t0: Wall-clock at the first sample of *this* block; provenance only.
        monotonic_ns: ``time.monotonic_ns()`` at ``read_started_at``.
        read_started_at: Wall-clock just before the read; provenance only.
        read_finished_at: Wall-clock just after the read; provenance only.
        elapsed_s: ``read_finished_at - read_started_at`` in seconds.
        units: Engineering units keyed by channel display name.
        error: Populated only under ``ErrorPolicy.RETURN``. Always ``None``
            under the default ``RAISE`` policy.
    """

    device: str
    task: str | None = None
    channels: tuple[str, ...]
    data: np.ndarray
    block_index: int
    first_sample_index: int
    samples_per_channel: int
    sample_rate_hz: float | None
    dt_s: float | None
    task_started_at: datetime
    t0: datetime
    monotonic_ns: int
    read_started_at: datetime
    read_finished_at: datetime
    elapsed_s: float
    units: Mapping[str, str | None]
    error: NIDaqError | None = None

    def __post_init__(self) -> None:
        """Validate the rectangular-shape invariant.

        Raises:
            NIDaqValidationError: ``data.shape`` does not equal
                ``(len(channels), samples_per_channel)``.
        """
        # Local import — keeps the model module from importing the errors
        # module at parse time to avoid circular-import surprises.
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
class DaqSample:
    """One scalar sample for one channel — the explicit-scalarization record.

    Produced **only** by ``block_to_long_rows(block)`` for users who deliberately
    want one row per (channel, sample) — e.g. low-rate logging into a CSV
    alongside Alicat / Sartorius rows. High-rate streaming should stay in
    :class:`DaqBlock` shape (design doc §8.9).

    Attributes:
        device: Manager-add name, or :attr:`TaskSpec.name`. Matches the
            :class:`DaqReading` join key.
        task: Underlying :attr:`TaskSpec.name`.
        channel: Display name of the channel this sample belongs to.
        value: The scalar value.
        acquired_at: Wall-clock reconstructed from
            ``task_started_at + sample_index / sample_rate_hz``.
        monotonic_ns: ``time.monotonic_ns()`` proxy reconstructed from the
            block's ``monotonic_ns`` plus ``sample_index * dt_s``.
        unit: Engineering unit, or ``None`` if the channel did not declare
            one.
        error: Populated only on error-tagged blocks under
            :class:`ErrorPolicy.RETURN`. Always ``None`` on success rows.
    """

    device: str
    task: str | None = None
    channel: str
    value: float | int | bool
    acquired_at: datetime
    monotonic_ns: int
    unit: str | None
    error: NIDaqError | None = None


__all__ = ["DaqBlock", "DaqReading", "DaqSample"]
