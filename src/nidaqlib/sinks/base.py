"""Sink Protocols, row helpers, and pipe drivers.

Two Protocols, one per input shape:

- :class:`ReadingSink` — accepts :class:`DaqReading` sequences.
- :class:`BlockSink` — accepts one :class:`DaqBlock` per call. No batching
  axis; a block is already ``(n_channels, n_samples)``.

Two drivers thread streams to sinks:

- :func:`pipe` — row-oriented, batched.
- :func:`pipe_blocks` — block-native, no batching axis.

Row helpers convert acquisition records into row dicts:

- :func:`reading_to_row` — flatten :class:`DaqReading` into one row.
- :func:`block_to_rows` — explicit per-(channel, sample) scalarisation
  of a :class:`DaqBlock` into a list of row dicts. Never invoked
  automatically; row-oriented sinks call it only when explicitly told to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

import anyio

from nidaqlib._logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading


__all__ = [
    "BlockSink",
    "ReadingSink",
    "block_to_rows",
    "pipe",
    "pipe_blocks",
    "reading_to_row",
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
class BlockSink(Protocol):
    """Sink that consumes one :class:`DaqBlock` per call.

    A block is already ``(n_channels, n_samples)`` — wrapping it in a
    sequence per call would burn allocations in the hot path. Sinks that
    need scalar rows opt in via :func:`block_to_rows`.
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
    - ``t_mono_ns`` — int, canonical monotonic join key.
    - ``t_utc`` — ISO 8601, wall-clock acquisition midpoint.
    - ``t_midpoint_mono_ns`` — int or None (integration-window midpoint).
    - ``requested_at`` / ``received_at`` — ISO 8601, I/O provenance.
    - ``latency_s`` — float seconds.
    - one column per channel (``values`` keys), values flattened.
    - one ``<channel>_unit`` column per channel.
    - ``error_type`` / ``error_message`` — populated only on error rows.

    The same row layout is used by every row-oriented sink.
    """
    row: dict[str, float | int | str | bool | None] = {
        "device": reading.device,
        "task": reading.task,
        "t_mono_ns": reading.t_mono_ns,
        "t_utc": reading.t_utc.isoformat(),
        "t_midpoint_mono_ns": reading.t_midpoint_mono_ns,
        "requested_at": reading.requested_at.isoformat(),
        "received_at": reading.received_at.isoformat(),
        "latency_s": reading.latency_s,
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


def block_to_rows(block: DaqBlock) -> list[dict[str, float | int | str | bool | None]]:
    """Unroll a :class:`DaqBlock` into one row per ``(channel, sample)``.

    Per-sample timestamps reconstruct from ``block.t_mono_ns``,
    ``block.block_period_ns``, and ``block.first_sample_index``. For
    on-demand blocks (no clock), samples are spaced uniformly within the
    read window.

    Each row carries:

    - ``device``, ``task``, ``channel`` — join keys.
    - ``block_index``, ``sample_index`` — block- and task-level indices.
    - ``t_mono_ns`` — reconstructed monotonic nanoseconds for this sample.
    - ``t_utc`` — reconstructed wall-clock (ISO 8601) for this sample.
    - ``value`` — the scalar sample value.
    - ``unit`` — engineering unit for the channel (or ``None``).
    - ``error_type`` / ``error_message`` — populated only on error blocks.
    """
    from datetime import timedelta  # noqa: PLC0415

    n_channels = len(block.channels)
    n_samples = block.samples_per_channel
    period_ns = block.block_period_ns
    if period_ns is None:
        span_ns = int((block.read_finished_at - block.read_started_at).total_seconds() * 1e9)
        period_ns = span_ns // max(1, n_samples)

    err = block.error
    err_type = f"{type(err).__module__}.{type(err).__qualname__}" if err is not None else None
    err_msg = str(err) if err is not None else None

    rows: list[dict[str, float | int | str | bool | None]] = []
    for c in range(n_channels):
        ch_name = block.channels[c]
        unit = block.units.get(ch_name)
        for k in range(n_samples):
            absolute = block.first_sample_index + k
            sample_t_mono_ns = block.t_mono_ns + k * period_ns
            sample_t_utc = block.t_utc + timedelta(microseconds=(k * period_ns) / 1_000)
            rows.append(
                {
                    "device": block.device,
                    "task": block.task,
                    "channel": ch_name,
                    "block_index": block.block_index,
                    "sample_index": absolute,
                    "t_mono_ns": sample_t_mono_ns,
                    "t_utc": sample_t_utc.isoformat(),
                    "value": float(block.data[c, k]),
                    "unit": unit,
                    "error_type": err_type,
                    "error_message": err_msg,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Pipe drivers
# ---------------------------------------------------------------------------


async def pipe(
    stream: AsyncIterator[DaqReading],
    sink: ReadingSink,
    *,
    batch_size: int = 64,
    flush_interval_s: float = 1.0,
) -> int:
    """Drain a :class:`DaqReading` stream into a row-oriented sink with buffered flushes.

    Reads records from ``stream`` and accumulates them into a list. A
    flush is triggered when either the buffer reaches ``batch_size`` or
    ``flush_interval_s`` elapses since the last flush. On stream
    exhaustion, the leftover buffer is flushed before returning.

    Args:
        stream: Async iterator of :class:`DaqReading` records.
        sink: An open :class:`ReadingSink`. The sink's ``write_many`` is
            awaited per flush.
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
    buffer: list[DaqReading] = []
    last_flush = anyio.current_time()

    async def _flush() -> None:
        nonlocal emitted
        if not buffer:
            return
        await sink.write_many(buffer)
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
