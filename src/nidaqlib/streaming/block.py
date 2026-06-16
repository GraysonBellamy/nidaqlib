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
    NIDaqTransientError,
)
from nidaqlib.streaming._types import Recording
from nidaqlib.tasks.models import DaqBlock
from nidaqlib.tasks.spec import AcquisitionMode

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import numpy as np
    from anyio.abc import TaskGroup
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from nidaqlib.tasks.session import DaqSession


class ErrorPolicy(StrEnum):
    """How recorders react to wrapped NI errors during a read."""

    RAISE = "raise"
    """Cancel the recorder's task group and re-raise the error."""

    RETURN = "return"
    """Emit a :class:`DaqBlock` (or :class:`DaqReading`) with ``.error`` set,
    then continue.

    The recorder MUST advance timing counters (``block_index`` /
    ``first_sample_index`` / ``t_mono_ns``) on error records so consumers
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


def _validate_record_state(
    source: DaqSession,
    *,
    error_policy: ErrorPolicy,
    use_callback_bridge: bool,
) -> None:
    """Reject lifecycle / option combinations the recorder cannot serve.

    Splits out of :func:`record` so the entry-point branch count stays under
    the project's lint cap.
    """
    if use_callback_bridge:
        if error_policy is ErrorPolicy.RETURN:
            raise NIDaqTaskStateError(
                "record(use_callback_bridge=True) does not support "
                "ErrorPolicy.RETURN — only RAISE is wired for the §11.3.2 "
                "bridge. Use the blocking-read path (use_callback_bridge=False) "
                "if you need RETURN semantics.",
                context=ErrorContext(task_name=source.spec.name, command_name="record"),
            )
        if not source.is_configured:
            raise NIDaqTaskStateError(
                f"record(use_callback_bridge=True) requires a configured session; "
                f"task {source.spec.name!r} is not configured",
                context=ErrorContext(task_name=source.spec.name, command_name="record"),
            )
        if source.is_started:
            raise NIDaqTaskStateError(
                f"record(use_callback_bridge=True) requires an unstarted session; "
                f"task {source.spec.name!r} is already started. "
                f"Use open_device(spec, autostart=False) to defer the start.",
                context=ErrorContext(task_name=source.spec.name, command_name="record"),
            )
    elif not source.is_started:
        raise NIDaqTaskStateError(
            f"record() requires a started session; task {source.spec.name!r} is not running",
            context=ErrorContext(task_name=source.spec.name, command_name="record"),
        )
    timing = source.spec.timing
    if timing is None or getattr(timing, "mode", None) == "on_demand":
        raise NIDaqTaskStateError(
            "record() requires a hardware-clocked task; use record_polled() for "
            "timing=None or AcquisitionMode.ON_DEMAND",
            context=ErrorContext(task_name=source.spec.name, command_name="record"),
        )


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
) -> AsyncGenerator[Recording[DaqBlock]]:
    """Hardware-clocked block acquisition.

    Yields a :class:`Recording[DaqBlock]`. The stream is closed when this
    context manager exits; ``summary`` is mutated in place during the run
    and is safe to read after exit.

    Args:
        source: A configured :class:`DaqSession`. Required state depends on
            ``use_callback_bridge``:

            * ``use_callback_bridge=False`` (Option A) — ``source`` must be
              **started**; wrap with :func:`~nidaqlib.tasks.open_device` (the
              default ``autostart=True``).
            * ``use_callback_bridge=True`` (Option B / §11.3.2) — ``source``
              must be **configured but not yet started**; pass
              ``autostart=False`` to ``open_device`` and let the recorder own
              the start. NI rejects buffer-event registration on a running
              task with -200960 ("Register all your DAQmx software events
              prior to starting the task").
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
        NIDaqTaskStateError: ``source`` is in the wrong lifecycle state for
            the selected mode (see ``source`` argument above).
        ValueError: ``chunk_size < 1`` or ``buffer_size < 1``.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size!r}")
    if buffer_size < 1:
        raise ValueError(f"buffer_size must be >= 1, got {buffer_size!r}")
    _validate_record_state(
        source,
        error_policy=error_policy,
        use_callback_bridge=use_callback_bridge,
    )

    summary = AcquisitionSummary()
    timing = source.spec.timing
    rate_hz: float | None = (
        timing.rate_hz
        if timing is not None and timing.mode is not AcquisitionMode.ON_DEMAND
        else None
    )
    tx, rx = anyio.create_memory_object_stream[DaqBlock](max_buffer_size=buffer_size)
    drop_rx = rx.clone()

    # TDMS LoggingMode.LOG bypasses the application read path. If we tried
    # to drive the producer, ``read_block`` would block forever waiting on
    # samples that never arrive. Detect at entry and emit an empty stream
    # so consumers see ``blocks_emitted == 0``.
    if _is_log_only(source):
        async with rx, drop_rx:
            await tx.aclose()
            try:
                yield Recording(stream=rx, summary=summary, rate_hz=rate_hz)
            finally:
                summary.finished_at = datetime.now(UTC)
        return

    async with anyio.create_task_group() as tg, rx, drop_rx:
        if use_callback_bridge:
            await _start_bridge_producer(tg, source, tx, drop_rx, summary, chunk_size, overflow)
        else:
            _ = tg.start_soon(
                _blocking_producer,
                source,
                tx,
                drop_rx,
                summary,
                chunk_size,
                timeout,
                overflow,
                error_policy,
            )
        try:
            yield Recording(stream=rx, summary=summary, rate_hz=rate_hz)
        finally:
            await tx.aclose()
            tg.cancel()
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
    drop_rx: MemoryObjectReceiveStream[DaqBlock],
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
            except (NIDaqReadError, NIDaqTransientError) as exc:
                summary.errors_observed += 1
                if isinstance(exc, NIDaqTransientError):
                    source._recoverable_error_count += 1  # pyright: ignore[reportPrivateUsage]
                if error_policy is ErrorPolicy.RAISE:
                    raise
                # RETURN — emit an error-tagged block. Counters still advance
                # so the consumer can detect dropped intervals.
                error_block = _build_error_block(source, chunk_size, exc)
                await _send_with_overflow(tx, drop_rx, error_block, summary, overflow)
                continue
            await _send_with_overflow(tx, drop_rx, block, summary, overflow)
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
    exc: NIDaqReadError | NIDaqTransientError,
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
    drop_rx: MemoryObjectReceiveStream[DaqBlock],
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
    # DROP_OLDEST: evict one queued block through a receive clone, then try
    # to enqueue the newest block without waiting on the consumer.
    while True:
        try:
            drop_rx.receive_nowait()
            summary.blocks_dropped += 1
        except anyio.WouldBlock:
            # Consumer won the race and made space after our failed send.
            pass
        try:
            tx.send_nowait(block)
            summary.blocks_emitted += 1
            return
        except anyio.WouldBlock:
            # Still full; loop and evict another queued item.
            continue
        except anyio.ClosedResourceError:
            raise


# ---------------------------------------------------------------------------
# Option B — every-N-samples callback bridge (§11.3.2)
# ---------------------------------------------------------------------------


async def _start_bridge_producer(
    tg: TaskGroup,
    source: DaqSession,
    tx: MemoryObjectSendStream[DaqBlock],
    drop_rx: MemoryObjectReceiveStream[DaqBlock],
    summary: AcquisitionSummary,
    chunk_size: int,
    overflow: OverflowPolicy,
) -> None:
    """Wire up the §11.3.2 driver-thread → ``queue.SimpleQueue`` bridge.

    Startup protocol (NI ordering — must match exactly):

    1. Register the NI callback (task is configured but NOT yet started — NI
       rejects -200960 if the task is running).
    2. Start the NI task (``source.start()``).
    3. Spawn drainer + cleanup tasks.

    Shutdown protocol (run in this function's cleanup task; the outer
    ``open_device`` exit then runs ``session.close`` which is a no-op for
    the already-stopped task):

    1. Stop the NI task — NI rejects unregister on a running task with
       -200986. After stop returns, no more callbacks fire.
    2. Unregister the NI callback.
    3. Put a sentinel on the queue (drainer wakes from ``queue.get``).
    4. Await the drainer's exit (no leaked worker thread).

    Both sequences are mandatory and ordered (design doc §9.2 / §11.3.2).
    """
    chunk_q: queue.SimpleQueue[object] = queue.SimpleQueue()
    drain_done = anyio.Event()

    # The user callback (`_on_buffer`) is held by this closure scope for the
    # lifetime of the bridge. The NI-side wrapper that NI stores as a raw C
    # function pointer is kept alive by the backend's _callback_wrappers
    # dict (keyed by id(task)) — see backend/nidaqmx_backend.py and §11.3.2.
    def _on_buffer(n: int) -> None:
        # Runs on a driver thread. No anyio. The driver guarantees `n`
        # samples are available; pull them with timeout=0 so a stop_task
        # racing with this read returns immediately rather than blocking.
        try:
            arr = source._backend.read_block(source.raw_task, n, 0.0)  # pyright: ignore[reportPrivateUsage]
        except (NIDaqReadError, NIDaqTransientError):
            # On error, enqueue the sentinel so the drainer exits cleanly
            # rather than spinning. The recorder __aexit__ will record the
            # error count via summary.errors_observed below.
            chunk_q.put_nowait(_SENTINEL)
            return
        chunk_q.put_nowait(arr)

    # NI ordering: register the buffer event BEFORE starting the task.
    # `source` enters this function configured-but-not-started; record()
    # has already validated that. After registration, start the task so the
    # callback begins firing and the §8.7 wall-clock anchor lands on the
    # session.
    handle = await run_sync(
        source._backend.register_every_n_samples,  # pyright: ignore[reportPrivateUsage]
        source.raw_task,
        chunk_size,
        _on_buffer,
    )
    try:
        await source.start()
    except BaseException:
        # If start fails, unregister so the outer close() can run cleanly
        # without an orphan callback firing against a torn-down task.
        with anyio.CancelScope(shield=True):
            await run_sync(
                source._backend.unregister_every_n_samples,  # pyright: ignore[reportPrivateUsage]
                source.raw_task,
                handle,
            )
        raise

    async def _drain() -> None:
        try:
            while True:
                arr = await run_sync(chunk_q.get)
                if arr is _SENTINEL:
                    return
                block = _build_block_from_array(source, arr, chunk_size)
                try:
                    await _send_with_overflow(tx, drop_rx, block, summary, overflow)
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
                # NI ordering on the way out:
                #   1. stop_task — NI rejects unregister on a running task
                #      with -200986. After stop returns, in-flight callbacks
                #      have completed and no new ones fire, so the
                #      "callback races sentinel" concern from the original
                #      design (unregister-first) cannot occur.
                #   2. unregister — drops NI's reference to the wrapper.
                #   3. sentinel — wakes the drainer from chunk_q.get().
                #   4. drain_done — wait for the drainer to exit cleanly.
                # session.close() checks ``_started`` before stop_task, so
                # the outer open_device exit will not stop again.
                await source.stop()
                await run_sync(
                    source._backend.unregister_every_n_samples,  # pyright: ignore[reportPrivateUsage]
                    source.raw_task,
                    handle,
                )
                chunk_q.put_nowait(_SENTINEL)
                await drain_done.wait()

    _ = tg.start_soon(_drain)
    _ = tg.start_soon(_cleanup_on_exit)


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
            context=ErrorContext(task_name=source.spec.name, command_name="record_bridge"),
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
