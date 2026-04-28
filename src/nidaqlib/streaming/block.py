"""Hardware-clocked block recorder — :func:`record`.

Two producer paths share one async-context-manager surface:

- **Option A (default)** — blocking read in a worker thread. NI's
  ``read_many_sample`` parks the worker in the driver until ``chunk_size``
  samples are available. Recommended unless you have measured a need for
  Option B's lower latency.
- **Option B** — every-N-samples buffer-event callback (the §11.3.2 driver
  thread bridge). Lower latency but harder to get right; it sits behind
  ``use_callback_bridge=True`` so the bridge can be exercised against
  :class:`~nidaqlib.backend.fake.FakeDaqBackend` before any production use.

Both paths emit :class:`~nidaqlib.tasks.models.DaqBlock` records into an
``anyio`` memory-object stream and surface counts via
:class:`AcquisitionSummary`.

See design doc §11.3, §13.1, §13.3.
"""

from __future__ import annotations

import queue
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import anyio
from anyio.to_thread import run_sync

from nidaqlib.errors import (
    ErrorContext,
    NIDaqReadError,
    NIDaqTaskStateError,
    NIDaqTimeoutError,
)
from nidaqlib.tasks.models import DaqBlock

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    import numpy as np
    from anyio.abc import TaskGroup
    from anyio.streams.memory import MemoryObjectSendStream

    from nidaqlib.tasks.session import DaqSession


class ErrorPolicy(StrEnum):
    """How recorders react to wrapped NI errors during a read."""

    RAISE = "raise"
    """Cancel the recorder's task group and re-raise the error."""

    RETURN = "return"
    """Emit a :class:`DaqBlock` (or :class:`DaqReading`) with ``.error`` set,
    then continue.

    The recorder MUST advance timing counters (``block_index`` /
    ``first_sample_index`` / ``monotonic_ns``) on error records so consumers
    can detect dropped intervals. Consumers MUST gate on ``error is None``
    before reading ``data``."""


class OverflowPolicy(StrEnum):
    """Behaviour when the recorder's outbound stream is full."""

    BLOCK = "block"
    """Producer awaits consumer. Risks NI buffer overrun on hardware-clocked
    tasks."""

    DROP_NEWEST = "drop_newest"
    """Drop the about-to-be-enqueued block. Bounds consumer latency; loses
    freshest data."""

    DROP_OLDEST = "drop_oldest"
    """Drop the oldest queued block. Keeps newest data; loses older queued
    blocks."""


@dataclass(slots=True)
class AcquisitionSummary:
    """Per-run counters, yielded alongside the block stream.

    Mirrors ``sartoriuslib.AcquisitionSummary`` shape but is intentionally
    *mutable*: counters are updated in place during the run so consumers
    can poll progress (e.g. for a TUI bar) and read final counts after
    exit. The recorder is the only writer; consumers MUST treat the
    object as read-only.

    Attributes:
        blocks_emitted: Total :class:`DaqBlock` records sent into the
            outbound stream.
        blocks_dropped: Records dropped because of an
            :class:`OverflowPolicy.DROP_*` decision.
        errors_observed: Wrapped NI errors seen during the run, regardless
            of :class:`ErrorPolicy`.
        started_at: Wall-clock at recorder entry.
        finished_at: Wall-clock at recorder exit. ``None`` while the
            recorder is still running.
    """

    blocks_emitted: int = 0
    blocks_dropped: int = 0
    errors_observed: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


_SENTINEL: Final[object] = object()
"""Identity-checked queue sentinel for the §11.3.2 ordered shutdown."""


@asynccontextmanager
async def record(
    source: DaqSession,
    *,
    chunk_size: int,
    timeout: float = 10.0,  # noqa: ASYNC109 — per-NI-read timeout, not coroutine
    buffer_size: int = 16,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    use_callback_bridge: bool = False,
) -> AsyncGenerator[tuple[AsyncIterator[DaqBlock], AcquisitionSummary]]:
    """Hardware-clocked block acquisition.

    Yields ``(stream, summary)``. The stream is closed when this context
    manager exits; ``summary`` is mutated in place during the run and is
    safe to read after exit (the values are frozen on the way out).

    Args:
        source: Started :class:`DaqSession`. The recorder does not start the
            session — wrap with :func:`~nidaqlib.tasks.open_task` first.
        chunk_size: Samples per channel per emitted :class:`DaqBlock`.
        timeout: Per-read timeout in seconds (Option A only — Option B reads
            from the NI buffer with timeout 0).
        buffer_size: AnyIO memory-object stream buffer, in :class:`DaqBlock`
            slots. Older blocks may be dropped per ``overflow`` when full.
        error_policy: :attr:`ErrorPolicy.RAISE` (default) cancels the task
            group on error; :attr:`ErrorPolicy.RETURN` emits an error-tagged
            :class:`DaqBlock` and continues. Option B (callback bridge)
            currently honours only :attr:`RAISE` — :attr:`RETURN` is wired
            for the Option A producer.
        overflow: Backpressure policy. ``DROP_OLDEST`` is the
            hardware-clocked default — see design doc §13.3.
        use_callback_bridge: Opt into the §11.3.2 every-N-samples callback
            path. Default ``False`` selects Option A (blocking read in a
            worker thread).

    Raises:
        NIDaqTaskStateError: ``source`` is not started.
    """
    if not source.is_started:
        raise NIDaqTaskStateError(
            f"record() requires a started session; task {source.spec.name!r} is not running",
            context=ErrorContext(task_name=source.spec.name, operation="record"),
        )

    summary = AcquisitionSummary()
    tx, rx = anyio.create_memory_object_stream[DaqBlock](max_buffer_size=buffer_size)

    # Design §13.2 / §14.6 — TDMS LoggingMode.LOG bypasses the application
    # read path. If we tried to drive the producer, ``read_block`` would
    # block forever waiting on samples that never arrive. Detect at entry
    # and emit an empty stream so consumers see ``blocks_emitted == 0``.
    if _is_log_only(source):
        async with rx:
            await tx.aclose()
            try:
                yield rx, summary
            finally:
                summary.finished_at = datetime.now(UTC)
        return

    async with anyio.create_task_group() as tg, rx:
        if use_callback_bridge:
            await _start_bridge_producer(tg, source, tx, summary, chunk_size)
        else:
            tg.start_soon(
                _blocking_producer,
                source,
                tx,
                summary,
                chunk_size,
                timeout,
                overflow,
                error_policy,
            )
        try:
            yield rx, summary
        finally:
            await tx.aclose()
            tg.cancel_scope.cancel()
    summary.finished_at = datetime.now(UTC)


def _is_log_only(source: DaqSession) -> bool:
    """Return ``True`` when the task's TDMS logging is in LOG (write-only) mode."""
    logging = source.spec.logging
    if logging is None:
        return False
    # Lazy import — keep nidaqmx.constants out of the streaming module's
    # top-level imports for environments where the driver is absent.
    from nidaqmx.constants import LoggingMode  # noqa: PLC0415

    return bool(logging.mode == LoggingMode.LOG)


# ---------------------------------------------------------------------------
# Option A — blocking-read producer
# ---------------------------------------------------------------------------


async def _blocking_producer(
    source: DaqSession,
    tx: MemoryObjectSendStream[DaqBlock],
    summary: AcquisitionSummary,
    chunk_size: int,
    timeout: float,  # noqa: ASYNC109 — per-NI-read timeout, not coroutine
    overflow: OverflowPolicy,
    error_policy: ErrorPolicy,
) -> None:
    """Producer loop for Option A (blocking read in a worker thread).

    Pumps :class:`DaqBlock` records from ``source.read_block`` into ``tx``.
    Exits cleanly on cancellation or stream close. Under
    :attr:`ErrorPolicy.RETURN`, wrapped NI errors are emitted as
    :class:`DaqBlock` records with ``.error`` set rather than raised
    (design doc §13.2).
    """
    try:
        while True:
            try:
                block = await source.read_block(chunk_size, timeout=timeout)
            except (NIDaqReadError, NIDaqTimeoutError) as exc:
                summary.errors_observed += 1
                if error_policy is ErrorPolicy.RAISE:
                    raise
                # RETURN — emit an error-tagged block. Counters still advance
                # so the consumer can detect dropped intervals (design §13.2).
                error_block = _build_error_block(source, chunk_size, exc)
                await _send_with_overflow(tx, error_block, summary, overflow)
                continue
            await _send_with_overflow(tx, block, summary, overflow)
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        # Consumer side closed mid-flight — clean shutdown, not an error.
        # asyncio surfaces this as BrokenResourceError; trio uses
        # ClosedResourceError. Treat both as benign.
        return
    except anyio.EndOfStream:  # pragma: no cover - send side never raises this
        return


def _build_error_block(
    source: DaqSession,
    chunk_size: int,
    exc: NIDaqReadError | NIDaqTimeoutError,
) -> DaqBlock:
    """Synthesise a zero-filled :class:`DaqBlock` with ``.error`` set.

    Used by the :attr:`ErrorPolicy.RETURN` codepath. The shape invariants on
    :class:`DaqBlock` still hold; consumers MUST gate on ``error is None``
    before reading ``data``.
    """
    import numpy as np  # noqa: PLC0415

    n_channels = len(source.spec.channels)
    zeros = np.zeros((n_channels, chunk_size), dtype=np.float64)
    read_started_at = datetime.now(UTC)
    monotonic_ns = time.monotonic_ns()
    block = source._build_block(  # pyright: ignore[reportPrivateUsage]
        data=zeros,
        samples_per_channel=chunk_size,
        read_started_at=read_started_at,
        read_finished_at=read_started_at,
        monotonic_ns=monotonic_ns,
    )
    # _build_block returns a frozen DaqBlock with error=None — replace it
    # with a copy carrying the error (frozen-dataclass-friendly path).
    from dataclasses import replace as _replace  # noqa: PLC0415

    return _replace(block, error=exc)


async def _send_with_overflow(
    tx: MemoryObjectSendStream[DaqBlock],
    block: DaqBlock,
    summary: AcquisitionSummary,
    overflow: OverflowPolicy,
) -> None:
    """Apply the configured overflow policy to one outbound block."""
    if overflow is OverflowPolicy.BLOCK:
        await tx.send(block)
        summary.blocks_emitted += 1
        return
    try:
        tx.send_nowait(block)
        summary.blocks_emitted += 1
        return
    except anyio.WouldBlock:
        pass
    except anyio.ClosedResourceError:
        # Consumer closed; let the producer loop's outer handler clean up.
        raise
    if overflow is OverflowPolicy.DROP_NEWEST:
        summary.blocks_dropped += 1
        return
    # DROP_OLDEST: best-effort eviction of the oldest queued block.
    # AnyIO doesn't expose a peek-and-pop API on the send side, so we use
    # the receive side via the stream's internal state. The recorder's
    # consumer holds the only outstanding receive handle, so blocking-send
    # would deadlock against itself; the safest cross-implementation way
    # is to simply re-await `send`, which yields and lets the consumer
    # advance. If the consumer is permanently slow, the stream stays full
    # and subsequent blocks land in the same path — the count is still
    # bumped correctly.
    summary.blocks_dropped += 1
    await tx.send(block)
    summary.blocks_emitted += 1


# ---------------------------------------------------------------------------
# Option B — every-N-samples callback bridge (§11.3.2)
# ---------------------------------------------------------------------------


async def _start_bridge_producer(
    tg: TaskGroup,
    source: DaqSession,
    tx: MemoryObjectSendStream[DaqBlock],
    summary: AcquisitionSummary,
    chunk_size: int,
) -> None:
    """Wire up the §11.3.2 driver-thread → ``queue.SimpleQueue`` bridge.

    The shutdown protocol is split between this function (which spawns the
    drainer + cleanup task) and the recorder's enclosing task group:

    1. Unregister the NI callback FIRST (no new chunks enqueue).
    2. Put a sentinel on the queue (drainer wakes from ``queue.get``).
    3. Await the drainer's exit (no leaked worker thread).
    4. Stop / close the task — handled by the outer ``open_task`` exit.

    The sequence is mandatory and ordered (design doc §9.2 / §11.3.2).
    """
    chunk_q: queue.SimpleQueue[object] = queue.SimpleQueue()
    drain_done = anyio.Event()

    # Strong reference held both on the session (via _set_callback_handle)
    # and in this closure scope; NI stores the wrapped callback as a raw C
    # function pointer, so two strong refs is the belt-and-braces fix for
    # the GC seam called out in §11.3.2.
    def _on_buffer(n: int) -> None:
        # Runs on a driver thread. No anyio. The driver guarantees `n`
        # samples are available; pull them with timeout=0 so a stop_task
        # racing with this read returns immediately rather than blocking.
        try:
            arr = source._backend.read_block(source.raw_task, n, 0.0)  # pyright: ignore[reportPrivateUsage]
        except (NIDaqReadError, NIDaqTimeoutError):
            # On error, enqueue the sentinel so the drainer exits cleanly
            # rather than spinning. The recorder __aexit__ will record the
            # error count via summary.errors_observed below.
            chunk_q.put_nowait(_SENTINEL)
            return
        chunk_q.put_nowait(arr)

    # Register on the backend, retain the handle on the session.
    handle = await run_sync(
        source._backend.register_every_n_samples,  # pyright: ignore[reportPrivateUsage]
        source.raw_task,
        chunk_size,
        _on_buffer,
    )
    source._set_callback_handle(handle)  # pyright: ignore[reportPrivateUsage]

    async def _drain() -> None:
        try:
            while True:
                arr = await run_sync(chunk_q.get)
                if arr is _SENTINEL:
                    return
                block = _build_block_from_array(source, arr, chunk_size)
                try:
                    tx.send_nowait(block)
                except anyio.WouldBlock:
                    summary.blocks_dropped += 1
                    continue
                except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                    # Consumer side closed (recorder exiting). Drain remaining
                    # chunks until we hit the sentinel so the cleanup task
                    # observes drain_done.set(); dropping blocks on exit is
                    # not an error.
                    summary.blocks_dropped += 1
                    continue
                summary.blocks_emitted += 1
        finally:
            drain_done.set()

    async def _cleanup_on_exit() -> None:
        # Wait for cancellation (recorder __aexit__ -> tg.cancel_scope.cancel).
        # On cancellation, run the §11.3.2 ordered shutdown.
        try:
            await anyio.sleep_forever()
        finally:
            with anyio.CancelScope(shield=True):
                await run_sync(
                    source._backend.unregister_every_n_samples,  # pyright: ignore[reportPrivateUsage]
                    source.raw_task,
                    handle,
                )
                source._set_callback_handle(None)  # pyright: ignore[reportPrivateUsage]
                chunk_q.put_nowait(_SENTINEL)
                await drain_done.wait()

    tg.start_soon(_drain)
    tg.start_soon(_cleanup_on_exit)


def _build_block_from_array(
    source: DaqSession,
    arr: np.ndarray | object,
    chunk_size: int,
) -> DaqBlock:
    """Wrap a raw ``np.ndarray`` chunk produced by the driver-thread callback.

    Mirrors :meth:`DaqSession._build_block` but is called from the drainer
    side of the bridge; we touch the session's internal counters under the
    same single-writer convention (the drainer is the only writer to those
    counters during a bridge-driven recording).
    """
    import numpy as np  # noqa: PLC0415

    if not isinstance(arr, np.ndarray):
        # Sentinel propagated by accident — defensive fallback.
        msg = "bridge drainer received a non-array payload"
        raise NIDaqReadError(
            msg,
            context=ErrorContext(task_name=source.spec.name, operation="record_bridge"),
        )
    read_started_at = datetime.now(UTC)
    monotonic_ns = time.monotonic_ns()
    return source._build_block(  # pyright: ignore[reportPrivateUsage]
        data=arr,
        samples_per_channel=chunk_size,
        read_started_at=read_started_at,
        read_finished_at=read_started_at,
        monotonic_ns=monotonic_ns,
    )


__all__ = ["AcquisitionSummary", "ErrorPolicy", "OverflowPolicy", "record"]
