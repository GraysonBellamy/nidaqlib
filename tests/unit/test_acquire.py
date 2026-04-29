"""Tests for :meth:`DaqSession.acquire` (finite, design doc §12.3)."""

from __future__ import annotations

import numpy as np
import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    NIDaqTaskStateError,
    TaskSpec,
    Timing,
    open_task,
)
from nidaqlib.backend import FakeDaqBackend


@pytest.mark.anyio
async def test_acquire_returns_block_and_stops_task() -> None:
    """Finite acquire reads the requested samples, then stops the task."""
    spec = TaskSpec(
        name="finite_ai",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.FINITE, samples_per_channel=200),
    )
    backend = FakeDaqBackend(
        blocks={"finite_ai": [np.zeros((1, 200), dtype=np.float64)]},
    )
    async with open_task(spec, backend=backend) as session:
        block = await session.acquire(samples_per_channel=200)
        assert block.samples_per_channel == 200
        assert block.data.shape == (1, 200)
        assert not session.is_started  # acquire stops the task on completion


@pytest.mark.anyio
async def test_finite_session_can_restart_after_acquire() -> None:
    """``acquire`` stops but does not poison the configured task for reuse."""
    spec = TaskSpec(
        name="finite_ai",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.FINITE, samples_per_channel=2),
    )
    backend = FakeDaqBackend(
        blocks={
            "finite_ai": [
                np.zeros((1, 2), dtype=np.float64),
                np.ones((1, 2), dtype=np.float64),
            ]
        },
    )
    async with open_task(spec, backend=backend) as session:
        first = await session.acquire(samples_per_channel=2)
        assert not session.is_started
        await session.start()
        second = await session.acquire(samples_per_channel=2)

    assert first.block_index == 0
    assert second.block_index == 0
    ops = [op.op for op in backend.operations]
    assert ops.count("create_task") == 1
    assert ops.count("start_task") == 2
    assert ops.count("stop_task") == 2


@pytest.mark.anyio
async def test_acquire_rejects_continuous_task() -> None:
    """``acquire`` requires ``Timing.mode == FINITE``."""
    spec = TaskSpec(
        name="cont_ai",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.CONTINUOUS),
    )
    backend = FakeDaqBackend(read_block_default_shape=(1, 100))
    async with open_task(spec, backend=backend) as session:
        with pytest.raises(NIDaqTaskStateError):
            await session.acquire(samples_per_channel=100)


@pytest.mark.anyio
async def test_acquire_rejects_no_timing() -> None:
    """A spec with ``timing=None`` is not eligible for finite acquisition."""
    spec = TaskSpec(
        name="ai_no_timing",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
    )
    backend = FakeDaqBackend(read_block_default_shape=(1, 100))
    async with open_task(spec, backend=backend) as session:
        with pytest.raises(NIDaqTaskStateError):
            await session.acquire(samples_per_channel=100)
