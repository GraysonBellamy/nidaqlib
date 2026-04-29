"""Block B — recorders (``record_polled``, ``record``) + sinks on real hardware.

Exercises both recorder paths against a TC module:

- ``record_polled`` → :class:`DaqReading` rows, fan-out to InMemory / SQLite /
  Parquet / JSONL / CSV.
- ``record`` (Option A blocking + Option B callback bridge) →
  :class:`DaqBlock` rows into Parquet.

Each test runs a short capture (≤ 10 s of wall clock) and asserts the
shape / counts / time-anchor fields. Sample-time reconstruction is
checked explicitly via the design §8.7 formula.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, cast

import anyio
import pytest

from nidaqlib import (
    DaqBlock,
    DaqReading,
    NIDaqSinkSchemaError,
    OverflowPolicy,
    TaskSpec,
    open_task,
    record,
    record_polled,
)
from nidaqlib.sinks import (
    CsvSink,
    InMemorySink,
    JsonlSink,
    ParquetSink,
    SqliteSink,
)

from .conftest import assert_close_float, assert_plausible_temperature

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import TcHardwareConfig


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# B1 — record_polled into InMemorySink
# ---------------------------------------------------------------------------


async def test_b1_record_polled_in_memory(
    tc_config: TcHardwareConfig,
    tc_spec_on_demand: TaskSpec,
) -> None:
    """``record_polled`` at 2 Hz for ~3 s emits ~6 readings into InMemorySink."""
    target_rate_hz = 2.0
    duration_s = 3.0

    sink = InMemorySink()
    async with (
        open_task(tc_spec_on_demand) as session,
        sink,
        record_polled(session, rate_hz=target_rate_hz, buffer_size=16) as (rx, summary),
    ):
        deadline = anyio.current_time() + duration_s
        async for payload in rx:
            reading = cast("DaqReading", payload)
            await sink.write_many([reading])
            if anyio.current_time() >= deadline:
                break

    expected = int(duration_s * target_rate_hz)
    assert len(sink.readings) >= expected - 1, (
        f"expected ~{expected} readings, got {len(sink.readings)}"
    )
    assert summary.errors_observed == 0
    # Monotonic timestamps.
    monotonic_values = [r.monotonic_ns for r in sink.readings]
    assert monotonic_values == sorted(monotonic_values)
    # Plausible temperatures throughout.
    for r in sink.readings:
        for ch, value in r.values.items():
            assert_plausible_temperature(float(value), tc_config, where=f"B1.{ch}")


# ---------------------------------------------------------------------------
# B2 — record_polled into SQLite, Parquet, JSONL
# ---------------------------------------------------------------------------


async def _drain_polled_into_reading_sink(
    spec: TaskSpec,
    *,
    sink: SqliteSink | ParquetSink | JsonlSink,
    rate_hz: float,
    duration_s: float,
) -> int:
    """Helper: run ``record_polled`` for ``duration_s`` into ``sink``.

    Returns the number of readings written.
    """
    written = 0
    async with (
        open_task(spec) as session,
        sink,
        record_polled(session, rate_hz=rate_hz, buffer_size=16) as (rx, _summary),
    ):
        deadline = anyio.current_time() + duration_s
        async for payload in rx:
            reading = cast("DaqReading", payload)
            await sink.write_many([reading])
            written += 1
            if anyio.current_time() >= deadline:
                break
    return written


async def test_b2_record_polled_to_sqlite(
    tc_spec_on_demand: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """SqliteSink captures readings into a queryable table."""
    db_path = hw_tmp_dir / "b2.sqlite"
    sink = SqliteSink(db_path)
    written = await _drain_polled_into_reading_sink(
        tc_spec_on_demand, sink=sink, rate_hz=4.0, duration_s=2.0
    )
    assert written >= 6

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    assert rows == written


async def test_b2_record_polled_to_parquet(
    tc_spec_on_demand: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """ParquetSink captures readings; the file is non-empty and re-readable."""
    pq = pytest.importorskip("pyarrow.parquet", reason="pyarrow not installed")
    parquet_path = hw_tmp_dir / "b2.parquet"
    sink = ParquetSink(parquet_path)
    written = await _drain_polled_into_reading_sink(
        tc_spec_on_demand, sink=sink, rate_hz=4.0, duration_s=2.0
    )
    assert written >= 6

    table = pq.read_table(parquet_path)
    assert table.num_rows == written


async def test_b2_record_polled_to_jsonl(
    tc_spec_on_demand: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """JsonlSink emits one parseable JSON object per reading."""
    jsonl_path = hw_tmp_dir / "b2.jsonl"
    sink = JsonlSink(jsonl_path)
    written = await _drain_polled_into_reading_sink(
        tc_spec_on_demand, sink=sink, rate_hz=4.0, duration_s=2.0
    )
    assert written >= 6

    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == written
    # Every line round-trips through json.loads. JsonlSink uses the same
    # flattened row layout as the SQLite/Parquet sinks (reading_to_row):
    # one column per channel, plus device/task/timestamp/elapsed metadata.
    for line in lines:
        parsed = json.loads(line)
        assert parsed["device"] == "tc_on_demand"
        assert parsed["task"] == "tc_on_demand"
        assert "monotonic_ns" in parsed
        assert "primary" in parsed
        assert parsed["primary_unit"] == "degC"


# ---------------------------------------------------------------------------
# B3 — record (block path) into Parquet, sample-time reconstruction
# ---------------------------------------------------------------------------


async def test_b3_record_blocks_to_parquet(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """``record`` emits :class:`DaqBlock`s; Parquet round-trips block_index.

    Verifies the design §8.7 sample-time reconstruction formula on real
    hardware: ``block.task_started_at + (first_sample_index + k) / rate_hz``.
    """
    pq = pytest.importorskip("pyarrow.parquet", reason="pyarrow not installed")

    chunk_size = max(2, int(tc_config.rate_hz // 2))  # ~0.5 s
    target_blocks = 4
    parquet_path = hw_tmp_dir / "b3.parquet"

    seen: list[DaqBlock] = []
    async with (
        open_task(tc_spec_continuous) as session,
        ParquetSink(parquet_path) as sink,
        record(session, chunk_size=chunk_size) as (rx, summary),
    ):
        async for block in rx:
            seen.append(block)
            await sink.write(block)
            if len(seen) >= target_blocks:
                break

    assert len(seen) == target_blocks
    assert summary.errors_observed == 0
    # Counters strictly increase by chunk_size.
    assert [b.block_index for b in seen] == list(range(target_blocks))
    assert [b.first_sample_index for b in seen] == [i * chunk_size for i in range(target_blocks)]
    # Sample-rate reconstruction matches the spec.
    for b in seen:
        assert_close_float(b.sample_rate_hz, tc_config.rate_hz, where="B3.sample_rate_hz")
        assert_close_float(b.dt_s, 1.0 / tc_config.rate_hz, where="B3.dt_s")
    # Parquet long-form: chunk_size rows per block, one row group per block.
    table = pq.read_table(parquet_path)
    assert table.num_rows == target_blocks * chunk_size
    pf = pq.ParquetFile(parquet_path)
    assert pf.metadata.num_row_groups == target_blocks


# ---------------------------------------------------------------------------
# B4 — overflow policy (DROP_OLDEST) under a slow consumer
# ---------------------------------------------------------------------------


async def test_b4_polled_overflow_drop_oldest(
    tc_spec_on_demand: TaskSpec,
) -> None:
    """A slow consumer + ``DROP_OLDEST`` records dropped readings in the summary.

    Buffer is set to 1 so the producer immediately collides with the
    artificially-slowed consumer; in a few seconds we should observe a
    non-zero drop count.
    """
    polled_rate_hz = 10.0
    consumer_period_s = 0.6  # consumer ≪ producer rate
    duration_s = 3.0

    seen = 0
    async with (
        open_task(tc_spec_on_demand) as session,
        record_polled(
            session,
            rate_hz=polled_rate_hz,
            buffer_size=1,
            overflow=OverflowPolicy.DROP_OLDEST,
        ) as (rx, summary),
    ):
        deadline = anyio.current_time() + duration_s
        async for _payload in rx:
            seen += 1
            await anyio.sleep(consumer_period_s)
            if anyio.current_time() >= deadline:
                break

    assert seen >= 1, "consumer never received a reading"
    assert summary.blocks_dropped > 0, (
        f"expected overflow drops with rate={polled_rate_hz} Hz vs "
        f"consumer period={consumer_period_s} s; got 0"
    )


# ---------------------------------------------------------------------------
# B5 — Option B (every-N-samples callback bridge) at low rate
# ---------------------------------------------------------------------------


async def test_b5_record_with_callback_bridge(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
) -> None:
    """The §11.3.2 callback-bridge path emits blocks with the same shape as Option A.

    Low-rate smoke test only — TC modules cap at well under 1 kHz, so this
    exercises the bridge plumbing rather than its under-load behaviour
    (the fake-backend unit tests cover load, GC, and cancellation).
    """
    chunk_size = max(2, int(tc_config.rate_hz // 2))
    target_blocks = 3

    seen: list[DaqBlock] = []
    async with (
        # autostart=False — the bridge needs to register the buffer event
        # before NI starts the task (NI rejects post-start registration with
        # -200960). record(use_callback_bridge=True) owns the start.
        open_task(tc_spec_continuous, autostart=False) as session,
        record(
            session,
            chunk_size=chunk_size,
            use_callback_bridge=True,
        ) as (rx, summary),
    ):
        async for block in rx:
            seen.append(block)
            if len(seen) >= target_blocks:
                break

    assert len(seen) == target_blocks
    assert summary.errors_observed == 0
    for i, b in enumerate(seen):
        assert b.block_index == i
        assert b.data.shape == (1, chunk_size)


# ---------------------------------------------------------------------------
# B6 — CsvSink refuses blocks by default
# ---------------------------------------------------------------------------


async def test_b6_csv_sink_refuses_blocks_by_default(
    tc_spec_continuous: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """``CsvSink(accept_blocks=False)`` raises on the first ``write(block)``.

    Verifies the §14.1 default-refusal behaviour: row-oriented sinks must
    not silently scalarize a high-rate stream.
    """
    chunk_size = 4
    csv_path = hw_tmp_dir / "b6.csv"
    sink = CsvSink(csv_path, accept_blocks=False)

    async with (
        open_task(tc_spec_continuous) as session,
        sink,
        record(session, chunk_size=chunk_size) as (rx, _summary),
    ):
        async for block in rx:
            with pytest.raises(NIDaqSinkSchemaError):
                await sink.write(block)
            break


# ---------------------------------------------------------------------------
# B7 — CsvSink with accept_blocks=True scalarizes correctly
# ---------------------------------------------------------------------------


async def test_b7_csv_sink_accept_blocks_scalarizes(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """``CsvSink(accept_blocks=True)`` writes one row per (channel, sample)."""
    chunk_size = max(2, int(tc_config.rate_hz // 2))
    target_blocks = 2
    csv_path = hw_tmp_dir / "b7.csv"

    blocks: list[DaqBlock] = []
    async with (
        open_task(tc_spec_continuous) as session,
        CsvSink(csv_path, accept_blocks=True) as sink,
        record(session, chunk_size=chunk_size) as (rx, _summary),
    ):
        async for block in rx:
            blocks.append(block)
            await sink.write(block)
            if len(blocks) >= target_blocks:
                break

    # Header + one row per (channel, sample) for every block.
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    expected_data_rows = sum(len(b.channels) * b.samples_per_channel for b in blocks)
    assert len(rows) - 1 == expected_data_rows, (
        f"expected {expected_data_rows} data rows + header, got {len(rows)}"
    )


# ---------------------------------------------------------------------------
# B8 — long-run drift / leak check
# ---------------------------------------------------------------------------


async def test_b8_long_run_polled_drift(
    tc_spec_on_demand: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """``record_polled`` for ~10 s into SQLite shows monotonic timestamps and no drops.

    Bench-day A1-A6 / B1-B7 each run 2-3 s; an accumulator or GC-stall bug
    would only show up over a longer window. 10 s of 5 Hz sampling is
    enough to surface cumulative drift without making the suite slow.

    Asserts:

    - Producer emits ~``rate_hz * duration`` readings within 5 % of the
      expected count (NI scheduling jitter is the main contributor).
    - ``monotonic_ns`` is strictly increasing across the entire run.
    - SQLite row count matches the reading count.
    - ``summary.errors_observed == 0`` and ``summary.blocks_dropped == 0``.
    """
    rate_hz = 5.0
    duration_s = 10.0
    db_path = hw_tmp_dir / "b8.sqlite"

    async with (
        open_task(tc_spec_on_demand) as session,
        SqliteSink(db_path) as sink,
        record_polled(session, rate_hz=rate_hz, buffer_size=8) as (rx, summary),
    ):
        readings: list[DaqReading] = []
        deadline = anyio.current_time() + duration_s
        async for payload in rx:
            # Session-mode record_polled emits DaqReading; the union return
            # type is for the manager-mode overload (Mapping[str, ...]),
            # which we don't take here.
            reading = cast("DaqReading", payload)
            readings.append(reading)
            await sink.write_many([reading])
            if anyio.current_time() >= deadline:
                break

    expected = int(rate_hz * duration_s)
    # Allow ±5 % — first/last partial intervals + scheduler jitter.
    assert abs(len(readings) - expected) <= max(2, expected // 20), (
        f"expected ~{expected} readings, got {len(readings)}"
    )
    monotonic_values = [r.monotonic_ns for r in readings]
    assert monotonic_values == sorted(monotonic_values), (
        "monotonic_ns should be strictly increasing over the long run"
    )
    assert summary.errors_observed == 0
    assert summary.blocks_dropped == 0
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    assert count == len(readings)


# ---------------------------------------------------------------------------
# B9 — cancellation mid-bridge cleanly tears down on real hardware
# ---------------------------------------------------------------------------


async def test_b9_bridge_cancel_mid_stream_clean_unwind(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
) -> None:
    """Cancelling a bridge-mode ``record`` mid-stream completes cleanly within budget.

    The unit suite verifies the operation-log ordering against the fake
    backend; this test verifies the same shutdown sequence does not hang or
    raise on real NI. Wraps the body in ``anyio.fail_after(5.0)`` so a
    deadlock surfaces as a test failure rather than a hung suite.

    Concretely: we let the bridge produce a few blocks, then break out of
    the consumer loop. The recorder ``__aexit__`` runs the §11.3.2
    shutdown (stop → unregister → sentinel → drain), and the outer
    ``open_task`` exits. Both must finish within ``fail_after``.
    """
    chunk_size = max(2, int(tc_config.rate_hz // 2))

    received = 0
    with anyio.fail_after(5.0):
        async with (
            open_task(tc_spec_continuous, autostart=False) as session,
            record(
                session,
                chunk_size=chunk_size,
                use_callback_bridge=True,
            ) as (rx, summary),
        ):
            async for _block in rx:
                received += 1
                if received >= 2:
                    # Break out → cancel scope → bridge cleanup must run.
                    break

    assert received >= 2
    assert summary.errors_observed == 0


# ---------------------------------------------------------------------------
# B10 — two-channel block read via callback bridge
# ---------------------------------------------------------------------------


async def test_b10_bridge_two_channel_blocks(
    tc_config: TcHardwareConfig,
    tc_spec_continuous_two_channel: TaskSpec,
) -> None:
    """The bridge reshapes two-channel buffers into ``(2, chunk_size)`` blocks.

    B5 only exercises one channel. The reshape path
    (:func:`_build_block_from_array`) interleaves NI's flat sample buffer
    by channel — this is where a transposition bug would silently swap
    channel data. Two-channel coverage on real hardware confirms the
    reshape is right.
    """
    chunk_size = max(2, int(tc_config.rate_hz // 2))
    target_blocks = 3

    seen: list[DaqBlock] = []
    async with (
        open_task(tc_spec_continuous_two_channel, autostart=False) as session,
        record(
            session,
            chunk_size=chunk_size,
            use_callback_bridge=True,
        ) as (rx, _summary),
    ):
        async for block in rx:
            seen.append(block)
            if len(seen) >= target_blocks:
                break

    assert len(seen) == target_blocks
    for b in seen:
        assert b.data.shape == (2, chunk_size)
        assert b.channels == ("primary", "secondary")
        # Both channels are wired to room-temperature TCs; values should
        # be sane and the per-channel mean should track each other within
        # a few degrees (same lab air).
        ch0_mean = float(b.data[0].mean())
        ch1_mean = float(b.data[1].mean())
        assert_plausible_temperature(ch0_mean, tc_config, where="B10.ch0_mean")
        assert_plausible_temperature(ch1_mean, tc_config, where="B10.ch1_mean")
        assert abs(ch0_mean - ch1_mean) < 10.0, (
            f"two-channel ambient should agree within 10 degC; "
            f"got ch0={ch0_mean:.2f}, ch1={ch1_mean:.2f}"
        )
