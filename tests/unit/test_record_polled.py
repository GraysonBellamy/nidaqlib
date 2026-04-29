"""Tests for :func:`record_polled` (design doc §13.1).

The polled recorder is a port of alicatlib's absolute-target loop.
Tests cover:

- Happy path: emits at the target cadence on an on-demand session.
- ``ErrorPolicy.RETURN`` produces an error-tagged :class:`DaqReading`.
- Validation: ``rate_hz <= 0`` and ``buffer_size < 1`` are rejected.
"""

from __future__ import annotations

from typing import cast

import anyio
import numpy as np
import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DaqReading,
    ErrorPolicy,
    NIDaqReadError,
    OverflowPolicy,
    TaskSpec,
    Timing,
    open_task,
    record_polled,
)
from nidaqlib.backend import FakeDaqBackend


def _make_spec() -> TaskSpec:
    return TaskSpec(
        name="polled_ai",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0", unit="V")],
        timing=Timing(rate_hz=10.0, mode=AcquisitionMode.ON_DEMAND),
    )


@pytest.mark.anyio
async def test_emits_readings_at_cadence() -> None:
    """Polled recorder emits readings — values come from the fake's poll path."""
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with (
        open_task(_make_spec(), backend=backend) as session,
        record_polled(session, rate_hz=50.0, buffer_size=8) as (rx, _summary),
    ):
        seen: list[DaqReading] = []
        async for payload in rx:
            seen.append(cast("DaqReading", payload))
            if len(seen) >= 3:
                break
    assert len(seen) == 3
    for r in seen:
        assert r.error is None
        assert "ch0" in r.values


@pytest.mark.anyio
async def test_error_policy_return_emits_error_reading() -> None:
    """``ErrorPolicy.RETURN`` produces a :class:`DaqReading` with ``.error`` set."""
    err = NIDaqReadError("bad poll")
    backend = FakeDaqBackend(
        blocks={"polled_ai": [np.full((1, 1), 3.0)]},
        read_errors={"polled_ai": [err]},
    )
    async with (
        open_task(_make_spec(), backend=backend) as session,
        record_polled(
            session,
            rate_hz=50.0,
            error_policy=ErrorPolicy.RETURN,
            buffer_size=4,
        ) as (rx, summary),
    ):
        seen: list[DaqReading] = []
        async for payload in rx:
            seen.append(cast("DaqReading", payload))
            if len(seen) >= 2:
                break
    # First reading carries the scripted error; second is normal.
    assert seen[0].error is err
    assert seen[0].values == {}
    assert seen[1].error is None
    assert summary.errors_observed >= 1


@pytest.mark.anyio
async def test_drop_oldest_does_not_block_producer() -> None:
    """A full outbound buffer should evict old readings instead of stalling."""
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with (
        open_task(_make_spec(), backend=backend) as session,
        record_polled(
            session,
            rate_hz=500.0,
            buffer_size=1,
            overflow=OverflowPolicy.DROP_OLDEST,
        ) as (rx, summary),
    ):
        await anyio.sleep(0.05)
        payload = await rx.__anext__()

    assert cast("DaqReading", payload).error is None
    assert summary.blocks_dropped > 0
    read_count = sum(1 for op in backend.operations if op.op == "read_block")
    assert read_count > 5


def test_invalid_rate_hz() -> None:
    """``rate_hz <= 0`` is rejected at call site."""

    async def _check() -> None:
        backend = FakeDaqBackend(read_block_default_shape=(1, 1))
        async with open_task(_make_spec(), backend=backend) as session:
            with pytest.raises(ValueError, match="rate_hz"):
                async with record_polled(session, rate_hz=0.0):
                    pass

    import anyio

    anyio.run(_check)
