"""Tests for the manager-mode fan-out of :func:`record_polled`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DaqManager,
    NIDaqTaskStateError,
    TaskResult,
    TaskSpec,
    Timing,
    record_polled,
)
from nidaqlib.backend import FakeDaqBackend

if TYPE_CHECKING:
    from nidaqlib.tasks.models import DaqReading


def _spec(name: str, channel: str) -> TaskSpec:
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel=channel, name=f"{name}_ch", unit="V")],
        timing=Timing(rate_hz=10.0, mode=AcquisitionMode.ON_DEMAND),
    )


@pytest.mark.anyio
async def test_manager_fanout_emits_per_task_mappings() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with DaqManager() as mgr:
        await mgr.add("a", _spec("a", "Dev1/ai0"), backend=backend)
        await mgr.add("b", _spec("b", "Dev1/ai1"), backend=backend)
        await mgr.start()
        async with record_polled(mgr, rate_hz=50.0, buffer_size=8) as (rx, _summary):
            seen: list[dict[str, TaskResult[DaqReading]]] = []
            async for payload in rx:
                # Manager mode payload is a Mapping[str, TaskResult[DaqReading]].
                assert isinstance(payload, dict)
                seen.append(payload)
                if len(seen) >= 3:
                    break
    assert len(seen) == 3
    for payload in seen:
        assert set(payload) == {"a", "b"}
        for r in payload.values():
            assert r.error is None
            assert r.value is not None


@pytest.mark.anyio
async def test_manager_with_no_tasks_rejected() -> None:
    async with DaqManager() as mgr:
        with pytest.raises(NIDaqTaskStateError, match="at least one task"):
            async with record_polled(mgr, rate_hz=10.0):
                pass


@pytest.mark.anyio
async def test_closed_manager_rejected() -> None:
    backend = FakeDaqBackend()
    mgr = DaqManager()
    await mgr.add("a", _spec("a", "Dev1/ai0"), backend=backend)
    await mgr.close()
    with pytest.raises(NIDaqTaskStateError, match="closed manager"):
        async with record_polled(mgr, rate_hz=10.0):
            pass
