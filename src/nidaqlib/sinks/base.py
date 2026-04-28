"""Sink Protocols, row helpers, and pipe drivers (design doc §14.1).

Three Protocols, one per input shape:

- :class:`ReadingSink` — accepts :class:`DaqReading` sequences. Mirrors the
  shape of sibling ``SampleSink`` from sartoriuslib.
- :class:`SampleSink` — accepts :class:`DaqSample` sequences (the explicit
  scalarisation row).
- :class:`BlockSink` — accepts one :class:`DaqBlock` per call. No batching
  axis; a block is already ``(n_channels, n_samples)``.

Two drivers thread streams to sinks:

- :func:`pipe` — row-oriented, batched.
- :func:`pipe_blocks` — block-native, no batching axis.

Row helpers convert acquisition records into row dicts:

- :func:`reading_to_row` — flatten :class:`DaqReading`.
- :func:`sample_to_row` — flatten :class:`DaqSample`.
- :func:`block_to_long_rows` — explicit per-(channel, sample) scalarisation.
  Never invoked automatically.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

import anyio

from nidaqlib._logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading, DaqSample


__all__ = [
    "BlockSink",
    "ReadingSink",
    "SampleSink",
    "block_to_long_rows",
    "pipe",
    "pipe_blocks",
    "reading_to_row",
    "sample_to_row",
]


_logger = get_logger("sinks")


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ReadingSink(Protocol):
    """Sink that consumes :class:`DaqReading` sequences."""

    async def open(self) -> None:
        """Allocate the sink's backing resource (file handle, DB conn, ...)."""
        ...

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append ``items`` to the sink."""
        ...

    async def close(self) -> None:
        """Flush and release the backing resource. Idempotent."""
        ...

    async def __aenter__(self) -> Self:
        """Open the sink and return ``self`` for chaining."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sink on exit."""
        ...


@runtime_checkable
class SampleSink(Protocol):
    """Sink that consumes :class:`DaqSample` sequences (one row per sample)."""

    async def open(self) -> None:
        """Allocate the sink's backing resource."""
        ...

    async def write_many(self, items: Sequence[DaqSample]) -> None:
        """Append ``items`` to the sink."""
        ...

    async def close(self) -> None:
        """Flush and release the backing resource. Idempotent."""
        ...

    async def __aenter__(self) -> Self:
        """Open the sink and return ``self`` for chaining."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sink on exit."""
        ...


@runtime_checkable
class BlockSink(Protocol):
    """Sink that consumes one :class:`DaqBlock` per call.

    A block is already ``(n_channels, n_samples)`` — wrapping it in a
    sequence per call would burn allocations in the hot path. Sinks that
    need scalar rows opt in via :func:`block_to_long_rows`.
    """

    async def open(self) -> None:
        """Allocate the sink's backing resource."""
        ...

    async def write(self, block: DaqBlock) -> None:
        """Append one :class:`DaqBlock` as a row group / file segment / row."""
        ...

    async def close(self) -> None:
        """Flush and release the backing resource. Idempotent."""
        ...

    async def __aenter__(self) -> Self:
        """Open the sink and return ``self`` for chaining."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sink on exit."""
        ...


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def reading_to_row(reading: DaqReading) -> dict[str, float | int | str | bool | None]:
    """Flatten a :class:`DaqReading` into a single row dict.

    Layout:

    - ``device``, ``task`` — join keys.
    - ``requested_at`` / ``received_at`` / ``midpoint_at`` — ISO 8601.
    - ``monotonic_ns`` — int.
    - ``elapsed_s`` — float seconds.
    - one column per channel (``values`` keys), values flattened.
    - one ``<channel>_unit`` column per channel.
    - ``error_type`` / ``error_message`` — populated only on error rows.

    The same row layout is used by every row-oriented sink.
    """
    row: dict[str, float | int | str | bool | None] = {
        "device": reading.device,
        "task": reading.task,
        "requested_at": reading.requested_at.isoformat(),
        "received_at": reading.received_at.isoformat(),
        "midpoint_at": reading.midpoint_at.isoformat(),
        "monotonic_ns": reading.monotonic_ns,
        "elapsed_s": reading.elapsed_s,
    }
    row.update(reading.values)
    row.update({f"{ch}_unit": unit for ch, unit in reading.units.items()})
    err = reading.error
    if err is not None:
        row["error_type"] = f"{type(err).__module__}.{type(err).__qualname__}"
        row["error_message"] = str(err)
    else:
        row["error_type"] = None
        row["error_message"] = None
    return row


def sample_to_row(sample: DaqSample) -> dict[str, float | int | str | bool | None]:
    """Flatten a :class:`DaqSample` into a single row dict."""
    row: dict[str, float | int | str | bool | None] = {
        "device": sample.device,
        "task": sample.task,
        "channel": sample.channel,
        "value": sample.value,
        "acquired_at": sample.acquired_at.isoformat(),
        "monotonic_ns": sample.monotonic_ns,
        "unit": sample.unit,
    }
    err = sample.error
    if err is not None:
        row["error_type"] = f"{type(err).__module__}.{type(err).__qualname__}"
        row["error_message"] = str(err)
    else:
        row["error_type"] = None
        row["error_message"] = None
    return row


def block_to_long_rows(block: DaqBlock) -> Iterator[DaqSample]:
    """Yield one :class:`DaqSample` per (channel, sample) in ``block``.

    Sample timestamps reconstruct from
    ``task_started_at + (first_sample_index + k) / sample_rate_hz``
    (design doc §8.7). Use this only when a row-oriented sink is the right
    target — the natural shape of a hardware-clocked block is rectangular,
    and fanning out 8 000 dataclass instances per second has a real cost.
    """
    # Late import — DaqSample lives in the tasks package, importing it at
    # module top would create a cycle through nidaqlib.streaming on systems
    # that import nidaqlib.sinks first.
    from nidaqlib.tasks.models import DaqSample as _DaqSample  # noqa: PLC0415

    n_channels = len(block.channels)
    n_samples = block.samples_per_channel
    rate_hz = block.sample_rate_hz
    if rate_hz is None:
        # Fall back to the read window — non-clocked blocks have no truer
        # per-sample timestamp anyway. The receive timestamps span the
        # short read window and we space samples uniformly within it.
        span = (block.read_finished_at - block.read_started_at).total_seconds()
        dt = span / max(1, n_samples)
    else:
        dt = 1.0 / rate_hz

    for c in range(n_channels):
        ch_name = block.channels[c]
        unit = block.units.get(ch_name)
        for k in range(n_samples):
            absolute = block.first_sample_index + k
            elapsed_s = absolute * dt if rate_hz is not None else k * dt
            sample_at = block.task_started_at + timedelta(seconds=elapsed_s)
            mono = block.monotonic_ns + int(k * dt * 1e9)
            value = float(block.data[c, k])
            yield _DaqSample(
                device=block.device,
                task=block.task,
                channel=ch_name,
                value=value,
                acquired_at=sample_at,
                monotonic_ns=mono,
                unit=unit,
                error=block.error,
            )


# ---------------------------------------------------------------------------
# Pipe drivers
# ---------------------------------------------------------------------------


async def pipe(
    stream: AsyncIterator[DaqReading | DaqSample],
    sink: ReadingSink | SampleSink,
    *,
    batch_size: int = 64,
    flush_interval_s: float = 1.0,
) -> int:
    """Drain a row stream into a row-oriented sink with buffered flushes.

    Reads records from ``stream`` and accumulates them into a list. A
    flush is triggered when either the buffer reaches ``batch_size`` or
    ``flush_interval_s`` elapses since the last flush. On stream
    exhaustion, the leftover buffer is flushed before returning.

    Args:
        stream: Async iterator of records (typically the receive end of a
            :func:`~nidaqlib.streaming.record_polled` recorder).
        sink: An open :class:`ReadingSink` or :class:`SampleSink`. The
            sink's ``write_many`` is awaited per flush.
        batch_size: Records per flush.
        flush_interval_s: Wall-clock seconds between flushes.

    Returns:
        Total records actually handed to the sink.

    Raises:
        ValueError: ``batch_size < 1`` or ``flush_interval_s <= 0``.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size!r}")
    if flush_interval_s <= 0:
        raise ValueError(f"flush_interval_s must be > 0, got {flush_interval_s!r}")

    emitted = 0
    buffer: list[DaqReading | DaqSample] = []
    last_flush = anyio.current_time()

    async def _flush() -> None:
        nonlocal emitted
        if not buffer:
            return
        # Mypy can't prove that buffer is homogeneous (it's a union), but the
        # caller is responsible for feeding one type per pipe — sinks accept
        # the corresponding type only.
        await sink.write_many(buffer)  # type: ignore[arg-type]
        emitted += len(buffer)
        buffer.clear()

    async for record in stream:
        buffer.append(record)
        now = anyio.current_time()
        if len(buffer) >= batch_size or (now - last_flush) >= flush_interval_s:
            await _flush()
            last_flush = now

    await _flush()
    _logger.info("sinks.pipe_done", extra={"records_emitted": emitted})
    return emitted


async def pipe_blocks(
    stream: AsyncIterator[DaqBlock],
    sink: BlockSink,
    *,
    flush_interval_s: float | None = None,
) -> int:
    """Drain a block stream into a :class:`BlockSink`.

    No batching axis — blocks are already batched. ``flush_interval_s`` is
    accepted for API symmetry with :func:`pipe` but currently unused; sinks
    that need periodic flush can implement their own.

    Returns:
        Total blocks written.
    """
    del flush_interval_s  # reserved for future per-sink flush helpers
    emitted = 0
    async for block in stream:
        await sink.write(block)
        emitted += 1
    _logger.info("sinks.pipe_blocks_done", extra={"blocks_emitted": emitted})
    return emitted
