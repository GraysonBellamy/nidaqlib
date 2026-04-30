"""Tests for the §17 safety gate on :meth:`DaqSession.write`."""

from __future__ import annotations

import pytest

from nidaqlib import (
    AnalogOutputVoltage,
    DigitalOutput,
    NIDaqConfirmationRequiredError,
    NIDaqValidationError,
    TaskSpec,
    open_device,
)
from nidaqlib.backend import FakeDaqBackend


def _ao_spec(*, requires_confirm: bool = True) -> TaskSpec:
    return TaskSpec(
        name="ao_test",
        channels=[
            AnalogOutputVoltage(
                physical_channel="Dev1/ao0",
                name="heater",
                min_val=-10.0,
                max_val=10.0,
                safe_min=0.0,
                safe_max=5.0,
                requires_confirm=requires_confirm,
            ),
        ],
    )


@pytest.mark.anyio
async def test_write_refuses_without_confirm() -> None:
    backend = FakeDaqBackend()
    async with await open_device(_ao_spec(), backend=backend) as session:
        with pytest.raises(NIDaqConfirmationRequiredError, match="confirm=True"):
            await session.write({"heater": 1.0})


@pytest.mark.anyio
async def test_write_refuses_out_of_range() -> None:
    backend = FakeDaqBackend()
    async with await open_device(_ao_spec(), backend=backend) as session:
        with pytest.raises(NIDaqValidationError, match="outside safe range"):
            await session.write({"heater": 9.0}, confirm=True)


@pytest.mark.anyio
async def test_write_accepts_in_range_with_confirm() -> None:
    backend = FakeDaqBackend()
    async with await open_device(_ao_spec(), backend=backend) as session:
        await session.write({"heater": 3.5}, confirm=True)
    # Operations log records the write.
    write_ops = [op for op in backend.operations if op.op == "write"]
    assert len(write_ops) == 1
    assert "heater" in (write_ops[0].detail or "")


@pytest.mark.anyio
async def test_write_rejects_unknown_keys() -> None:
    backend = FakeDaqBackend()
    async with await open_device(_ao_spec(), backend=backend) as session:
        with pytest.raises(NIDaqValidationError, match="unknown="):
            await session.write({"heater": 1.0, "ghost": 2.0}, confirm=True)


@pytest.mark.anyio
async def test_write_rejects_missing_keys() -> None:
    spec = TaskSpec(
        name="ao_two",
        channels=[
            AnalogOutputVoltage(
                physical_channel="Dev1/ao0",
                name="ch_a",
                safe_min=0.0,
                safe_max=5.0,
            ),
            AnalogOutputVoltage(
                physical_channel="Dev1/ao1",
                name="ch_b",
                safe_min=0.0,
                safe_max=5.0,
            ),
        ],
    )
    backend = FakeDaqBackend()
    async with await open_device(spec, backend=backend) as session:
        with pytest.raises(NIDaqValidationError, match="missing="):
            await session.write({"ch_a": 1.0}, confirm=True)


@pytest.mark.anyio
async def test_write_does_not_silently_clamp() -> None:
    """The library MUST raise on out-of-range, never silently clamp."""
    backend = FakeDaqBackend()
    async with await open_device(_ao_spec(), backend=backend) as session:
        with pytest.raises(NIDaqValidationError):
            await session.write({"heater": -1.0}, confirm=True)
    # No write should have hit the backend.
    assert all(op.op != "write" for op in backend.operations)


@pytest.mark.anyio
async def test_digital_output_requires_confirm_by_default() -> None:
    spec = TaskSpec(
        name="do_test",
        channels=[
            DigitalOutput(physical_channel="Dev1/port0/line0", name="valve"),
        ],
    )
    backend = FakeDaqBackend()
    async with await open_device(spec, backend=backend) as session:
        with pytest.raises(NIDaqConfirmationRequiredError, match="confirm=True"):
            await session.write({"valve": True})
        await session.write({"valve": True}, confirm=True)
    write_ops = [op for op in backend.operations if op.op == "write"]
    assert len(write_ops) == 1


@pytest.mark.anyio
async def test_digital_output_optout_of_confirm() -> None:
    """Channels can opt out of ``requires_confirm`` for non-actuating lines."""
    spec = TaskSpec(
        name="do_led",
        channels=[
            DigitalOutput(
                physical_channel="Dev1/port0/line7",
                name="led",
                requires_confirm=False,
            ),
        ],
    )
    backend = FakeDaqBackend()
    async with await open_device(spec, backend=backend) as session:
        # No confirm needed.
        await session.write({"led": True})


@pytest.mark.anyio
async def test_write_rejected_when_no_outputs() -> None:
    from nidaqlib import AnalogInputVoltage

    spec = TaskSpec(
        name="ai_only",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0")],
    )
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(spec, backend=backend) as session:
        with pytest.raises(NIDaqValidationError, match="no output channels"):
            await session.write({"ch0": 1.0}, confirm=True)
