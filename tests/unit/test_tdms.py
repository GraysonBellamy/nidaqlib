"""Tests for :class:`TdmsLogging` and the recorder LOG short-circuit.

Covers design doc §13.2 / §14.6:

- ``TdmsLogging`` round-trips through dict.
- ``DaqSession`` configures TDMS in the right order (channels → logging → timing).
- ``record()`` detects ``LoggingMode.LOG`` and emits an empty stream rather than
  blocking forever in ``read_block``.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    LoggingMode,
    LoggingOperation,
    TaskSpec,
    TdmsLogging,
    Timing,
    open_device,
    record,
)
from nidaqlib.backend import FakeDaqBackend


def test_round_trip() -> None:
    """``TdmsLogging.to_dict`` → ``from_dict`` is the identity."""
    tdms = TdmsLogging(
        path="/tmp/run.tdms",
        operation=LoggingOperation.CREATE_OR_REPLACE,
        mode=LoggingMode.LOG_AND_READ,
        group_name="data",
    )
    assert TdmsLogging.from_dict(tdms.to_dict()) == tdms


def test_taskspec_round_trip_with_logging() -> None:
    """:attr:`TaskSpec.logging` survives a serialisation round-trip."""
    spec = TaskSpec(
        name="ai_with_tdms",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0),
        logging=TdmsLogging(path="/tmp/run.tdms"),
    )
    assert TaskSpec.from_dict(spec.to_dict()) == spec


@pytest.mark.anyio
async def test_session_configures_logging_in_order() -> None:
    """Session calls add_channel → configure_logging → configure_timing in order."""
    spec = TaskSpec(
        name="ai_logged",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0),
        logging=TdmsLogging(path="/tmp/run.tdms"),
    )
    backend = FakeDaqBackend(read_block_default_shape=(1, 100))
    async with await open_device(spec, backend=backend):
        ops = [op.op for op in backend.operations]
        # add_channel must precede configure_logging which must precede configure_timing.
        assert ops.index("add_channel") < ops.index("configure_logging")
        assert ops.index("configure_logging") < ops.index("configure_timing")


@pytest.mark.anyio
async def test_record_short_circuits_on_log_only() -> None:
    """LoggingMode.LOG → empty stream, no deadlock (design doc §13.2)."""
    spec = TaskSpec(
        name="ai_log_only",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.CONTINUOUS),
        logging=TdmsLogging(path="/tmp/run.tdms", mode=LoggingMode.LOG),
    )
    # No scripted blocks queued — if the recorder tried to call read_block we'd
    # get an exception. The short-circuit must keep that path cold.
    backend = FakeDaqBackend()
    async with (
        await open_device(spec, backend=backend) as session,
        record(session, chunk_size=100, buffer_size=2) as _rec,
    ):
        rx, summary = _rec.stream, _rec.summary
        blocks = [b async for b in rx]
        assert blocks == []
        assert summary.blocks_emitted == 0
