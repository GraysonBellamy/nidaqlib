"""Tests for :class:`DaqSession` lifecycle and ``poll`` state-guard.

Covers design doc §9.2 — ``poll()`` is invalid mid-buffered-acquisition.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DigitalInput,
    NIDaqTaskStateError,
    NIDaqValidationError,
    TaskSpec,
    Timing,
    open_device,
)
from nidaqlib.backend import FakeDaqBackend


def _make_spec(*, mode: AcquisitionMode | None) -> TaskSpec:
    timing = Timing(rate_hz=1000.0, mode=mode) if mode is not None else None
    return TaskSpec(
        name="ai_test",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0", unit="V")],
        timing=timing,
    )


@pytest.mark.anyio
async def test_poll_rejected_for_continuous_task() -> None:
    """Continuous + started → :class:`NIDaqTaskStateError`. Design doc §9.2."""
    spec = _make_spec(mode=AcquisitionMode.CONTINUOUS)
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(spec, backend=backend) as session:
        with pytest.raises(NIDaqTaskStateError):
            await session.poll()


@pytest.mark.anyio
async def test_poll_rejected_for_finite_task() -> None:
    spec = _make_spec(mode=AcquisitionMode.FINITE)
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(spec, backend=backend) as session:
        with pytest.raises(NIDaqTaskStateError):
            await session.poll()


@pytest.mark.anyio
async def test_poll_works_for_on_demand_task() -> None:
    spec = _make_spec(mode=AcquisitionMode.ON_DEMAND)
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(spec, backend=backend) as session:
        reading = await session.poll()
    assert reading.device == "ai_test"
    assert "ch0" in reading.values
    assert "configure_timing" not in [op.op for op in backend.operations]


@pytest.mark.anyio
async def test_poll_works_for_no_timing_task() -> None:
    """A task with no Timing is on-demand by definition."""
    spec = _make_spec(mode=None)
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(spec, backend=backend) as session:
        reading = await session.poll()
    assert reading.values["ch0"] is not None


@pytest.mark.anyio
async def test_session_state_after_close() -> None:
    spec = _make_spec(mode=AcquisitionMode.CONTINUOUS)
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    async with await open_device(spec, backend=backend) as session:
        await session.read_block(10)
    assert session.is_closed
    assert not session.is_started
    with pytest.raises(NIDaqTaskStateError):
        await session.read_block(10)


@pytest.mark.anyio
async def test_block_index_and_first_sample_advance() -> None:
    spec = _make_spec(mode=AcquisitionMode.CONTINUOUS)
    backend = FakeDaqBackend(read_block_default_shape=(1, 100))
    async with await open_device(spec, backend=backend) as session:
        b0 = await session.read_block(100)
        b1 = await session.read_block(100)
    assert b0.block_index == 0
    assert b0.first_sample_index == 0
    assert b1.block_index == 1
    assert b1.first_sample_index == 100


@pytest.mark.anyio
async def test_read_rejects_non_analog_input_task() -> None:
    spec = TaskSpec(
        name="di_test",
        channels=[DigitalInput(physical_channel="Dev1/port0/line0", name="line0")],
    )
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(spec, backend=backend) as session:
        with pytest.raises(NIDaqValidationError, match="analog-input"):
            await session.poll()
