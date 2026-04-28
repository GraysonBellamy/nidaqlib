#!/usr/bin/env python3
"""Combined alicatlib + sartoriuslib + nidaqlib acquisition.

The headline multi-source workflow that motivated `nidaqlib`. Three
co-located libraries cooperate inside one :func:`anyio.create_task_group`:

- ``AlicatManager`` streams MFC samples at 5 Hz into SQLite.
- ``SartoriusManager`` streams balance samples at 2 Hz into SQLite.
- A single ``open_task(...)`` materialises a 1 kHz two-channel analog-input
  task; ``record(daq_session, chunk_size=1000)`` lands `DaqBlock` rows in a
  sidecar Parquet file.

The asymmetry between the side-load (`record(mfc_mgr, rate_hz=…)`) and the
DAQ-side (`record(daq_session, chunk_size=…)`) call sites is intentional:

- For serial instruments, the manager owns the lifecycle of N devices and
  the recorder schedules per-device polls. ``record(mfcs, rate_hz=…)`` reads
  at the cadence the user requests.
- For NI DAQ, the hardware sample clock owns timing. The recorder reads
  blocks of ``chunk_size`` samples whenever NI signals they are available.
  A single ``open_task`` is the compact shape for the "single DAQ card,
  multiple serial instruments" pattern most labs run; ``DaqManager`` is
  available when the DAQ side needs fan-out across multiple tasks.

Required env vars:

    PORT_MFC=/dev/ttyUSB0 PORT_BAL=/dev/ttyUSB1 \\
        DAQ_AI0=Dev1/ai0 DAQ_AI1=Dev1/ai1 \\
        uv run python examples/combined_mfc_balance_daq.py

This example uses :class:`SqliteSink` from ``sartoriuslib`` for the scalar
samples and writes the DAQ-side blocks into a Parquet file directly via
``pyarrow`` so the data model is visible in one file.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import anyio
import numpy as np

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DaqBlock,
    TaskSpec,
    Timing,
    open_task,
    record,
)


async def _drain_daq_to_parquet(
    stream: object,  # AsyncIterator[DaqBlock]
    parquet_path: Path,
) -> int:
    """Pull blocks off the recorder stream and append them to a Parquet file.

    Returns the number of blocks written so the caller can print a run
    summary.
    """
    try:
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - example-only path
        msg = (
            "this example needs pyarrow; install nidaqlib's parquet extra "
            "(`uv add nidaqlib[parquet]`)"
        )
        raise SystemExit(msg) from exc

    blocks_written = 0
    writer: pq.ParquetWriter | None = None

    async for block in stream:  # type: ignore[attr-defined]
        assert isinstance(block, DaqBlock)
        # Long-format: one row per sample-channel pair, with the §8.7
        # reconstruction formula applied to recover sample-level wall-clock.
        n_samples = block.samples_per_channel
        n_channels = len(block.channels)
        rows = n_samples * n_channels
        sample_idx = np.arange(n_samples) + block.first_sample_index
        # Broadcast: (n_channels, n_samples) flattened in C order.
        channel_col = np.repeat(np.array(block.channels, dtype=object), n_samples)
        sample_col = np.tile(sample_idx, n_channels)
        value_col = np.asarray(block.data, dtype=np.float64).reshape(rows)
        device_col = np.full(rows, block.device, dtype=object)
        task_col = np.full(rows, block.task or "", dtype=object)
        block_col = np.full(rows, block.block_index, dtype=np.int64)
        elapsed_s = sample_col / (block.sample_rate_hz or 1.0)
        # task_started_at is wall-clock; build sample timestamps.
        anchor_ns = int(block.task_started_at.timestamp() * 1_000_000_000)
        sample_ts_ns = anchor_ns + (elapsed_s * 1_000_000_000).astype(np.int64)

        table = pa.table(
            {
                "device": device_col,
                "task": task_col,
                "channel": channel_col,
                "block_index": block_col,
                "sample_index": sample_col,
                "value": value_col,
                "sample_at_ns": sample_ts_ns,
            }
        )
        if writer is None:
            writer = pq.ParquetWriter(parquet_path, table.schema)
        writer.write_table(table)
        blocks_written += 1

    if writer is not None:
        writer.close()
    return blocks_written


async def main() -> None:
    """Headline example entry point.

    Brings up MFC + balance + DAQ together. Joins on `(device, monotonic_ns)`
    are left to downstream analysis — this script just persists the
    aligned streams.
    """
    # Lazy imports of the sibling packages so users without them installed
    # can still read this file as a reference.
    from alicatlib import AlicatManager  # noqa: PLC0415
    from alicatlib.sinks import SqliteSink as AlicatSqliteSink  # noqa: PLC0415
    from alicatlib.sinks import pipe as alicat_pipe  # noqa: PLC0415
    from alicatlib.streaming import record as alicat_record  # noqa: PLC0415
    from sartoriuslib import SartoriusManager  # noqa: PLC0415
    from sartoriuslib.sinks import SqliteSink, pipe  # noqa: PLC0415
    from sartoriuslib.streaming import record as sartorius_record  # noqa: PLC0415

    port_mfc = os.environ["PORT_MFC"]
    port_bal = os.environ["PORT_BAL"]
    daq_ai0 = os.environ.get("DAQ_AI0", "Dev1/ai0")
    daq_ai1 = os.environ.get("DAQ_AI1", "Dev1/ai1")
    duration_s = float(os.environ.get("DURATION", "30"))
    db_path = os.environ.get("OUTPUT", "combined_run.db")
    parquet_path = Path(os.environ.get("OUTPUT_PARQUET", "combined_run_daq.parquet"))

    daq_spec = TaskSpec(
        name="ai_pair",
        channels=[
            AnalogInputVoltage(physical_channel=daq_ai0, name="ch0", unit="V"),
            AnalogInputVoltage(physical_channel=daq_ai1, name="ch1", unit="V"),
        ],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.CONTINUOUS),
    )

    started = datetime.now(UTC)

    async with (
        AlicatManager() as mfcs,
        SartoriusManager() as bals,
        open_task(daq_spec) as daq_session,
    ):
        await mfcs.add("fuel", port_mfc)
        await bals.add("scale", port_bal)

        async with (
            alicat_record(mfcs, rate_hz=5.0, duration=duration_s) as mfc_stream,
            sartorius_record(bals, rate_hz=2.0, duration=duration_s) as bal_stream,
            record(daq_session, chunk_size=1000, buffer_size=32) as (daq_stream, _summary),
            AlicatSqliteSink(db_path, table="mfc_samples") as mfc_sink,
            SqliteSink(db_path, table="balance_samples") as bal_sink,
            anyio.create_task_group() as tg,
        ):

            async def _drain_mfc() -> None:
                summary = await alicat_pipe(mfc_stream, mfc_sink)
                print(f"mfc samples_emitted: {summary.samples_emitted}")

            async def _drain_bal() -> None:
                summary = await pipe(bal_stream, bal_sink)
                print(f"balance samples_emitted: {summary.samples_emitted}")

            async def _drain_daq() -> None:
                # Bound the DAQ run by the same duration as the others so
                # the example terminates predictably.
                with anyio.move_on_after(duration_s):
                    n = await _drain_daq_to_parquet(daq_stream, parquet_path)
                    print(f"daq blocks_written: {n}")

            tg.start_soon(_drain_mfc)
            tg.start_soon(_drain_bal)
            tg.start_soon(_drain_daq)

    elapsed = (datetime.now(UTC) - started).total_seconds()
    print(f"\nrun finished in {elapsed:.1f}s")
    print(f"  sqlite : {db_path}")
    print(f"  parquet: {parquet_path}")
    with sqlite3.connect(db_path) as conn:
        (mfc_count,) = conn.execute("SELECT COUNT(*) FROM mfc_samples").fetchone()
        (bal_count,) = conn.execute("SELECT COUNT(*) FROM balance_samples").fetchone()
    print(f"  mfc_samples      {mfc_count} rows")
    print(f"  balance_samples  {bal_count} rows")


if __name__ == "__main__":
    anyio.run(main)
