"""Tests for the sink family (design doc §14.1).

Covers:

- ``InMemorySink`` accepts all three shapes.
- ``CsvSink`` / ``JsonlSink`` refuse :class:`DaqBlock` by default;
  ``accept_blocks=True`` opts into per-(channel, sample) rows.
- ``SqliteSink`` writes readings, samples, and block summary rows into
  three different tables.
- ``ParquetSink`` writes a long-format table whose ``block_index`` /
  ``sample_index`` columns reconstruct sample timestamps via §8.7.
- ``pipe`` and ``pipe_blocks`` happy-path drivers.
- ``block_to_long_rows`` per-(channel, sample) fan-out.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np
import pytest

from nidaqlib import DaqBlock, DaqReading, NIDaqSinkSchemaError
from nidaqlib.sinks import (
    CsvSink,
    InMemorySink,
    JsonlSink,
    ParquetSink,
    SqliteSink,
    block_to_long_rows,
    pipe,
    pipe_blocks,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path


class _ParquetColumn(Protocol):
    def to_pylist(self) -> list[object]:
        """Return column values as Python scalars."""
        ...


class _ParquetTable(Protocol):
    @property
    def num_rows(self) -> int:
        """Number of table rows."""
        ...

    @property
    def column_names(self) -> list[str]:
        """Table column names."""
        ...

    def column(self, name: str) -> _ParquetColumn:
        """Return a named column."""
        ...


def _read_parquet_table(path: Path) -> _ParquetTable:
    pq = pytest.importorskip("pyarrow.parquet", reason="pyarrow not installed")
    read_table = cast("Callable[[Path], _ParquetTable]", vars(pq)["read_table"])
    return read_table(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reading() -> DaqReading:
    now = datetime.now(UTC)
    return DaqReading(
        device="dev1",
        task="dev1",
        values={"ai0": 1.5},
        units={"ai0": "V"},
        requested_at=now,
        received_at=now,
        midpoint_at=now,
        monotonic_ns=10,
        latency_s=0.001,
    )


def _block(*, block_index: int = 0, first_sample_index: int = 0) -> DaqBlock:
    now = datetime.now(UTC)
    rate_hz = 1000.0
    return DaqBlock(
        device="dev1",
        task="dev1",
        channels=("ai0", "ai1"),
        data=np.array(
            [
                [0.0, 1.0, 2.0],
                [10.0, 20.0, 30.0],
            ],
            dtype=np.float64,
        ),
        block_index=block_index,
        first_sample_index=first_sample_index,
        samples_per_channel=3,
        sample_rate_hz=rate_hz,
        dt_s=1.0 / rate_hz,
        task_started_at=now,
        t0=now,
        monotonic_ns=20,
        read_started_at=now,
        read_finished_at=now,
        elapsed_s=0.001,
        units={"ai0": "V", "ai1": "V"},
    )


# ---------------------------------------------------------------------------
# In-memory sink
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_in_memory_collects_all_shapes() -> None:
    async with InMemorySink() as sink:
        await sink.write_many([_reading()])
        await sink.write(_block())
    assert len(sink.readings) == 1
    assert len(sink.blocks) == 1


# ---------------------------------------------------------------------------
# block_to_long_rows
# ---------------------------------------------------------------------------


def test_block_to_long_rows_count_and_indices() -> None:
    block = _block()
    samples = list(block_to_long_rows(block))
    assert len(samples) == 2 * 3  # n_channels * samples_per_channel
    # Channels grouped together, samples in order within each channel.
    assert samples[0].channel == "ai0"
    assert samples[3].channel == "ai1"
    # Reconstructed timestamps advance by dt_s.
    delta = (samples[1].acquired_at - samples[0].acquired_at).total_seconds()
    assert math.isclose(delta, 1.0 / 1000.0)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_csv_refuses_block_by_default(tmp_path: Path) -> None:
    async with CsvSink(tmp_path / "out.csv") as sink:
        with pytest.raises(NIDaqSinkSchemaError):
            await sink.write(_block())


@pytest.mark.anyio
async def test_csv_accept_blocks_emits_long_rows(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    async with CsvSink(out, accept_blocks=True) as sink:
        await sink.write(_block())
    lines = out.read_text().strip().splitlines()
    # Header + (n_channels * n_samples) rows.
    assert len(lines) == 1 + 2 * 3


@pytest.mark.anyio
async def test_csv_writes_readings(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    async with CsvSink(out) as sink:
        await sink.write_many([_reading(), _reading()])
    text = out.read_text().strip().splitlines()
    assert len(text) == 3  # header + 2 rows


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_jsonl_refuses_block_by_default(tmp_path: Path) -> None:
    async with JsonlSink(tmp_path / "out.jsonl") as sink:
        with pytest.raises(NIDaqSinkSchemaError):
            await sink.write(_block())


@pytest.mark.anyio
async def test_jsonl_writes_readings(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    async with JsonlSink(out) as sink:
        await sink.write_many([_reading()])
    rows = [json.loads(line) for line in out.read_text().strip().splitlines()]
    assert len(rows) == 1
    assert rows[0]["device"] == "dev1"


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sqlite_separate_tables_per_shape(tmp_path: Path) -> None:
    out = tmp_path / "out.sqlite"
    async with SqliteSink(out) as sink:
        await sink.write_many([_reading()])
        await sink.write(_block())

    conn = sqlite3.connect(out)
    try:
        names = sorted(
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )
        assert names == ["blocks", "readings"]
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.anyio
async def test_sqlite_quotes_channel_derived_columns(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    reading = DaqReading(
        device="dev1",
        task="dev1",
        values={'bad"column': 1.0},
        units={'bad"column': "V"},
        requested_at=now,
        received_at=now,
        midpoint_at=now,
        monotonic_ns=10,
        latency_s=0.001,
    )
    out = tmp_path / "quoted.sqlite"
    async with SqliteSink(out) as sink:
        await sink.write_many([reading])

    conn = sqlite3.connect(out)
    try:
        assert conn.execute('SELECT "bad""column" FROM readings').fetchone()[0] == 1.0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parquet
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_parquet_block_round_trip_reconstructs_timestamps(tmp_path: Path) -> None:
    """5 blocks → block_index/first_sample_index monotonic, time_s ladders."""
    out = tmp_path / "out.parquet"
    blocks = [_block(block_index=i, first_sample_index=i * 3) for i in range(5)]
    async with ParquetSink(out) as sink:
        for b in blocks:
            await sink.write(b)
    table = _read_parquet_table(out)
    # 5 blocks * 2 channels * 3 samples = 30 rows.
    assert table.num_rows == 30
    columns = table.column_names
    assert {"block_index", "sample_index", "time_s", "channel", "value"} <= set(columns)

    block_indices = cast("list[int]", table.column("block_index").to_pylist())
    assert sorted(set(block_indices)) == [0, 1, 2, 3, 4]

    # Pull ai0 rows out and verify sample_index / time_s ladder.
    from itertools import pairwise

    channels = cast("list[str]", table.column("channel").to_pylist())
    sample_indices = cast("list[int]", table.column("sample_index").to_pylist())
    times = cast("list[float]", table.column("time_s").to_pylist())
    rows = list(zip(channels, sample_indices, times, strict=True))
    ai0_rows = sorted((s, t) for ch, s, t in rows if ch == "ai0")
    assert [s for s, _ in ai0_rows] == list(range(15))
    deltas = [b - a for (_, a), (_, b) in pairwise(ai0_rows)]
    assert all(abs(d - 0.001) < 1e-9 for d in deltas)


@pytest.mark.anyio
async def test_parquet_refuses_mixed_shapes(tmp_path: Path) -> None:
    """First write locks the shape; mixing later raises ``NIDaqSinkSchemaError``."""
    out = tmp_path / "out.parquet"
    async with ParquetSink(out) as sink:
        await sink.write(_block())
        with pytest.raises(NIDaqSinkSchemaError):
            await sink.write_many([_reading()])


# ---------------------------------------------------------------------------
# pipe / pipe_blocks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pipe_drains_readings_to_sink() -> None:
    async def _stream() -> AsyncIterator[DaqReading]:
        for _ in range(5):
            yield _reading()

    async with InMemorySink() as sink:
        n = await pipe(_stream(), sink, batch_size=2)
    assert n == 5
    assert len(sink.readings) == 5


@pytest.mark.anyio
async def test_pipe_blocks_drains_blocks_to_sink() -> None:
    async def _stream() -> AsyncIterator[DaqBlock]:
        for i in range(3):
            yield _block(block_index=i)

    async with InMemorySink() as sink:
        n = await pipe_blocks(_stream(), sink)
    assert n == 3
    assert len(sink.blocks) == 3


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pipe_validates_args() -> None:
    async def _stream() -> AsyncIterator[DaqReading]:
        # Empty async-generator: the ``yield`` is unreachable at runtime but
        # its presence makes Python treat ``_stream`` as an async generator.
        empty: tuple[DaqReading, ...] = ()
        for r in empty:  # pragma: no cover
            yield r
        return

    async with InMemorySink() as sink:
        with pytest.raises(ValueError, match="batch_size"):
            await pipe(_stream(), sink, batch_size=0)
        with pytest.raises(ValueError, match="flush_interval_s"):
            await pipe(_stream(), sink, flush_interval_s=0.0)
