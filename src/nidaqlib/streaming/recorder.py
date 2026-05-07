"""Software-timed scalar recorder — :func:`record_polled`.

Direct port of alicatlib's absolute-target loop (design doc §11.3, §13.1).
Drives a :class:`~nidaqlib.tasks.session.DaqSession` at a fixed
``rate_hz`` cadence and emits one :class:`DaqReading` per tick.

Key invariants (design §13.2):

- **Absolute-target scheduling.** Targets are computed from
  :func:`anyio.current_time` at recorder entry; drift across cycles is
  bounded by one tick and does not accumulate.
- **Software-timed only.** ``record_polled`` requires a session with
  ``Timing is None`` or ``Timing.mode == ON_DEMAND`` — buffered tasks
  must use :func:`~nidaqlib.streaming.record` for the high-rate path.
  :meth:`DaqSession.poll` enforces the same rule on its own.
- **Backpressure default differs from** :func:`~nidaqlib.streaming.record`.
  ``record_polled`` defaults to :attr:`OverflowPolicy.BLOCK` because the
  software-timed path can pause without leaking into NI buffer overrun
  (design doc §13.3).

When ``source`` is a manager, ``record_polled`` fans out across all managed
tasks at the same ``rate_hz`` and emits
``Mapping[str, DeviceResult[DaqReading]]`` per tick — matching
:meth:`DaqManager.poll`'s shape so a single ``error_policy`` decision is
consistent across the per-tick / one-shot paths.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Union

import anyio

from nidaqlib.errors import (
    ErrorContext,
    NIDaqError,
    NIDaqReadError,
    NIDaqTaskStateError,
    NIDaqTimeoutError,
)
from nidaqlib.streaming.block import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
)
from nidaqlib.tasks.models import DaqReading

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Mapping

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from nidaqlib.manager import DaqManager, DeviceResult
    from nidaqlib.tasks.session import DaqSession

__all__ = ["record_polled"]


# Per-tick payload type. Session mode emits :class:`DaqReading`; manager
# mode emits a per-task mapping so consumers can correlate readings across
# tasks at the same tick.
_PolledItem = Union["DaqReading", "Mapping[str, DeviceResult[DaqReading]]"]


@asynccontextmanager
async def record_polled(
    source: DaqSession | DaqManager,
    *,
    rate_hz: float,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
    buffer_size: int = 64,
) -> AsyncGenerator[tuple[AsyncIterator[_PolledItem], AcquisitionSummary]]:
    """Software-timed scalar polling at ``rate_hz``.

    Yields ``(stream, summary)``. The per-tick payload depends on
    ``source``:

    - :class:`DaqSession` → one :class:`DaqReading` per tick.
    - :class:`DaqManager` → ``Mapping[str, DeviceResult[DaqReading]]`` per
      tick (matches :meth:`DaqManager.poll`).

    ``summary`` is updated in place during the run; a final snapshot is
    frozen on exit.

    Args:
        source: A started :class:`DaqSession` (whose timing is ``None`` or
            :attr:`AcquisitionMode.ON_DEMAND`) or a :class:`DaqManager`.
        rate_hz: Target poll rate, in Hz. Must be > 0.
        error_policy: :attr:`RAISE` cancels the recorder on a poll error;
            :attr:`RETURN` emits a :class:`DaqReading` (or per-task
            :class:`DeviceResult` row) with the error attached and continues.
        overflow: Backpressure policy. Defaults to :attr:`BLOCK` —
            software-timed pollers can pause safely (design §13.3).
        buffer_size: AnyIO send-stream capacity in payload slots.

    Raises:
        NIDaqTaskStateError: A session ``source`` is not started, or a
            manager ``source`` is closed / has no managed tasks.
        ValueError: ``rate_hz <= 0`` or ``buffer_size < 1``.
    """
    if rate_hz <= 0:
        raise ValueError(f"rate_hz must be > 0, got {rate_hz!r}")
    if buffer_size < 1:
        raise ValueError(f"buffer_size must be >= 1, got {buffer_size!r}")

    # Late import — manager imports streaming.block, which lives in this same
    # package. Doing the import here keeps the package import graph acyclic.
    from nidaqlib.manager import DaqManager  # noqa: PLC0415

    if isinstance(source, DaqManager):
        if source.is_closed:
            raise NIDaqTaskStateError(
                "record_polled() requires an open DaqManager; got a closed manager",
                context=ErrorContext(operation="record_polled"),
            )
        if not source.names:
            raise NIDaqTaskStateError(
                "record_polled() requires a DaqManager with at least one task",
                context=ErrorContext(operation="record_polled"),
            )
    elif not source.is_started:
        raise NIDaqTaskStateError(
            f"record_polled() requires a started session; task {source.spec.name!r} is not running",
            context=ErrorContext(task_name=source.spec.name, operation="record_polled"),
        )

    summary = AcquisitionSummary()
    period = 1.0 / rate_hz
    tx, rx = anyio.create_memory_object_stream[_PolledItem](max_buffer_size=buffer_size)
    drop_rx = rx.clone()

    async with anyio.create_task_group() as tg, rx, drop_rx:
        if isinstance(source, DaqManager):
            tg.start_soon(
                _polled_manager_producer,
                source,
                tx,
                drop_rx,
                summary,
                period,
                error_policy,
                overflow,
            )
        else:
            tg.start_soon(
                _polled_producer,
                source,
                tx,
                drop_rx,
                summary,
                period,
                error_policy,
                overflow,
            )
        try:
            yield rx, summary
        finally:
            await tx.aclose()
            tg.cancel_scope.cancel()
    summary.finished_at = datetime.now(UTC)


async def _polled_producer(
    source: DaqSession,
    tx: MemoryObjectSendStream[_PolledItem],
    drop_rx: MemoryObjectReceiveStream[_PolledItem],
    summary: AcquisitionSummary,
    period: float,
    error_policy: ErrorPolicy,
    overflow: OverflowPolicy,
) -> None:
    """Drive the absolute-target poll loop for a session source.

    ``anyio.current_time`` and ``anyio.sleep_until`` share a clock so the
    target arithmetic and the sleep dispatch agree on "now."
    """
    start = anyio.current_time()
    tick = 0
    try:
        while True:
            target = start + tick * period
            now = anyio.current_time()
            if now > target + period:
                # Overran by more than one full period — skip to the next
                # valid slot rather than catching up. Skipped slots count as
                # dropped readings.
                missed = int((now - target) / period)
                summary.blocks_dropped += missed
                tick += missed
                target = start + tick * period
            if anyio.current_time() < target:
                await anyio.sleep_until(target)
            try:
                reading = await source.poll()
            except (NIDaqReadError, NIDaqTimeoutError) as exc:
                summary.errors_observed += 1
                if error_policy is ErrorPolicy.RAISE:
                    raise
                reading = _build_error_reading(source, exc)
            await _send_payload(tx, drop_rx, reading, summary, overflow)
            tick += 1
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        # Consumer closed mid-flight — asyncio surfaces this as
        # BrokenResourceError, trio as ClosedResourceError.
        return
    except anyio.EndOfStream:  # pragma: no cover
        return


async def _polled_manager_producer(
    manager: DaqManager,
    tx: MemoryObjectSendStream[_PolledItem],
    drop_rx: MemoryObjectReceiveStream[_PolledItem],
    summary: AcquisitionSummary,
    period: float,
    error_policy: ErrorPolicy,
    overflow: OverflowPolicy,
) -> None:
    """Drive the absolute-target poll loop for a manager source.

    Each tick fans out a :meth:`DaqManager.poll` and emits the resulting
    ``Mapping[str, DeviceResult[DaqReading]]``. ``error_policy`` is forwarded
    to the manager call — under :attr:`RETURN` per-task errors land in
    individual :class:`DeviceResult` rows; under :attr:`RAISE` they re-raise
    as an :class:`ExceptionGroup`.
    """
    start = anyio.current_time()
    tick = 0
    try:
        while True:
            target = start + tick * period
            now = anyio.current_time()
            if now > target + period:
                missed = int((now - target) / period)
                summary.blocks_dropped += missed
                tick += missed
                target = start + tick * period
            if anyio.current_time() < target:
                await anyio.sleep_until(target)
            results = await manager.poll(error_policy=error_policy)
            errors_this_tick = sum(1 for r in results.values() if r.error is not None)
            summary.errors_observed += errors_this_tick
            await _send_payload(tx, drop_rx, results, summary, overflow)
            tick += 1
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        return
    except anyio.EndOfStream:  # pragma: no cover
        return


def _build_error_reading(source: DaqSession, exc: NIDaqError) -> DaqReading:
    """Synthesise a :class:`DaqReading` carrying ``.error`` for RETURN policy."""
    now = datetime.now(UTC)
    import time as _time  # noqa: PLC0415

    return DaqReading(
        device=source.spec.name,
        task=source.spec.name,
        values={},
        units={ch.display_name: ch.unit for ch in source.spec.channels},
        requested_at=now,
        received_at=now,
        midpoint_at=now,
        monotonic_ns=_time.monotonic_ns(),
        latency_s=0.0,
        metadata=dict(source.spec.metadata),
        error=exc,
    )


async def _send_payload(
    tx: MemoryObjectSendStream[_PolledItem],
    drop_rx: MemoryObjectReceiveStream[_PolledItem],
    payload: _PolledItem,
    summary: AcquisitionSummary,
    overflow: OverflowPolicy,
) -> None:
    """Apply the configured overflow policy to one outbound payload."""
    if overflow is OverflowPolicy.BLOCK:
        await tx.send(payload)
        summary.blocks_emitted += 1
        return
    try:
        tx.send_nowait(payload)
        summary.blocks_emitted += 1
        return
    except anyio.WouldBlock:
        pass
    if overflow is OverflowPolicy.DROP_NEWEST:
        summary.blocks_dropped += 1
        return
    # DROP_OLDEST — evict from a receive clone, then enqueue without
    # waiting on the consumer.
    while True:
        try:
            drop_rx.receive_nowait()
            summary.blocks_dropped += 1
        except anyio.WouldBlock:
            pass
        try:
            tx.send_nowait(payload)
            summary.blocks_emitted += 1
            return
        except anyio.WouldBlock:
            continue
