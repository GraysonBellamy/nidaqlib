"""Tests for :meth:`DaqManager.start_synchronized` (§4.3).

Verifies the strict ordering invariant — every slave's ``start_task``
precedes the master's — and the failure-rollback path: when a slave
fails to arm, every previously armed slave is stopped and the master is
not started.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    AnalogInputVoltage,
    DaqManager,
    DigitalEdgeStartTrigger,
    Edge,
    ErrorPolicy,
    NIDaqTaskStateError,
    TaskSpec,
    Timing,
)
from nidaqlib.backend import FakeDaqBackend


def _ai_spec(name: str, *, channel: str, source: str | None = None) -> TaskSpec:
    timing = Timing(rate_hz=1000.0, source=source)
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel=channel)],
        timing=timing,
    )


def _trigger_spec(name: str, *, channel: str, trigger_source: str) -> TaskSpec:
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel=channel)],
        timing=Timing(rate_hz=1000.0),
        trigger=DigitalEdgeStartTrigger(source=trigger_source, edge=Edge.RISING),
    )


@pytest.mark.anyio
async def test_synchronized_starts_slaves_before_master() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    async with DaqManager() as mgr:
        await mgr.add("master", _ai_spec("master", channel="Dev1/ai0"), backend=backend)
        await mgr.add(
            "slave_a",
            _trigger_spec("slave_a", channel="Dev2/ai0", trigger_source="/Dev1/ai/StartTrigger"),
            backend=backend,
        )
        await mgr.add(
            "slave_b",
            _trigger_spec("slave_b", channel="Dev3/ai0", trigger_source="/Dev1/ai/StartTrigger"),
            backend=backend,
        )
        results = await mgr.start_synchronized("master", ["slave_a", "slave_b"])

        assert all(r.ok for r in results.values())

        starts = [op for op in backend.operations if op.op == "start_task"]
        order = [op.task_name for op in starts]
        assert order == ["slave_a", "slave_b", "master"]


@pytest.mark.anyio
async def test_synchronized_master_in_slaves_rejected() -> None:
    async with DaqManager() as mgr:
        backend = FakeDaqBackend(read_block_default_shape=(1, 10))
        await mgr.add("master", _ai_spec("master", channel="Dev1/ai0"), backend=backend)
        await mgr.add(
            "slave",
            _ai_spec("slave", channel="Dev2/ai0"),
            backend=backend,
        )
        with pytest.raises(NIDaqTaskStateError):
            await mgr.start_synchronized("master", ["master", "slave"])


@pytest.mark.anyio
async def test_synchronized_unknown_name_rejected() -> None:
    async with DaqManager() as mgr:
        backend = FakeDaqBackend(read_block_default_shape=(1, 10))
        await mgr.add("master", _ai_spec("master", channel="Dev1/ai0"), backend=backend)
        with pytest.raises(KeyError):
            await mgr.start_synchronized("master", ["does-not-exist"])


@pytest.mark.anyio
async def test_synchronized_rollback_on_slave_arm_failure() -> None:
    """Slave_b fails to start → slave_a must be stopped, master never started."""
    # Inject a start-time failure on slave_b's underlying create_task. Easiest
    # path: pre-create a fake task with the same name so backend.create_task
    # raises a duplicate-name error from inside ``DaqSession.start``.
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    async with DaqManager() as mgr:
        await mgr.add("master", _ai_spec("master", channel="Dev1/ai0"), backend=backend)
        await mgr.add("slave_a", _ai_spec("slave_a", channel="Dev2/ai0"), backend=backend)
        await mgr.add("slave_b", _ai_spec("slave_b", channel="Dev3/ai0"), backend=backend)

        # Force a failure on slave_b's create_task by pre-creating it.
        backend.create_task("slave_b")

        with pytest.raises(BaseExceptionGroup):
            await mgr.start_synchronized("master", ["slave_a", "slave_b"])

        # slave_a was armed, then must have been stopped during rollback.
        ops = [(op.op, op.task_name) for op in backend.operations]
        assert ("start_task", "slave_a") in ops
        assert ("stop_task", "slave_a") in ops
        # Master must NOT have been started.
        assert ("start_task", "master") not in ops


@pytest.mark.anyio
async def test_synchronized_return_policy_collects_errors() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    async with DaqManager(error_policy=ErrorPolicy.RETURN) as mgr:
        await mgr.add("master", _ai_spec("master", channel="Dev1/ai0"), backend=backend)
        await mgr.add("slave_a", _ai_spec("slave_a", channel="Dev2/ai0"), backend=backend)
        backend.create_task("slave_a")  # force failure

        results = await mgr.start_synchronized("master", ["slave_a"])
        assert results["slave_a"].error is not None
        # Master result is a synthetic "not started" error.
        assert results["master"].error is not None
        assert isinstance(results["master"].error, NIDaqTaskStateError)
