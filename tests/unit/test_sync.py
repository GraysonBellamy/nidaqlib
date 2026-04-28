"""Smoke tests for the sync facade — :class:`Daq`, sync :func:`record`."""

from __future__ import annotations

from nidaqlib import AnalogInputVoltage, DaqBlock, TaskSpec, Timing
from nidaqlib.backend import FakeDaqBackend
from nidaqlib.sync import Daq
from nidaqlib.sync import record as sync_record


def _make_spec() -> TaskSpec:
    return TaskSpec(
        name="sync_ai",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0),
    )


def test_sync_open_task_read_block() -> None:
    """:meth:`Daq.open_task` yields a sync session with a working ``read_block``."""
    backend = FakeDaqBackend(read_block_default_shape=(1, 100))
    with Daq.open_task(_make_spec(), backend=backend) as session:
        block = session.read_block(samples_per_channel=100)
        assert block.samples_per_channel == 100


def test_sync_record_iterates_blocks() -> None:
    """Sync :func:`record` yields a sync iterator the user can ``for``-loop over."""
    backend = FakeDaqBackend(read_block_default_shape=(1, 50))
    with (
        Daq.open_task(_make_spec(), backend=backend) as session,
        sync_record(session, chunk_size=50, buffer_size=4) as (stream, _summary),
    ):
        seen: list[DaqBlock] = []
        for block in stream:
            seen.append(block)
            if len(seen) >= 3:
                break
    assert len(seen) == 3
