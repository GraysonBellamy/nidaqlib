"""Tests for :attr:`ErrorPolicy.RETURN` in :func:`record` (design doc §13.2).

Under :attr:`RETURN`, wrapped NI errors become :class:`DaqBlock` records
with ``.error`` set, the recorder advances counters, and the producer
keeps running.
"""

from __future__ import annotations

import numpy as np
import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DaqBlock,
    ErrorPolicy,
    NIDaqReadError,
    TaskSpec,
    Timing,
    open_device,
    record,
)
from nidaqlib.backend import FakeDaqBackend


def _make_spec() -> TaskSpec:
    return TaskSpec(
        name="ai_err",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.CONTINUOUS),
    )


@pytest.mark.anyio
async def test_return_emits_error_block_and_continues() -> None:
    """One scripted error → one error-tagged block, then a normal block."""
    err = NIDaqReadError("bad read")
    backend = FakeDaqBackend(
        blocks={"ai_err": [np.full((1, 50), 7.0, dtype=np.float64)]},
        read_errors={"ai_err": [err]},
    )
    async with (
        await open_device(_make_spec(), backend=backend) as session,
        record(
            session,
            chunk_size=50,
            buffer_size=4,
            error_policy=ErrorPolicy.RETURN,
        ) as _rec,
    ):
        rx, summary = _rec.stream, _rec.summary
        seen: list[DaqBlock] = []
        async for block in rx:
            seen.append(block)
            if len(seen) >= 2:
                break

    assert len(seen) == 2
    # First block is the error one (the scripted error fires first).
    assert seen[0].error is err
    assert seen[0].data.shape == (1, 50)
    assert seen[1].error is None
    assert summary.errors_observed >= 1
    # block_index advances even on error rows.
    assert seen[0].block_index == 0
    assert seen[1].block_index == 1


@pytest.mark.anyio
async def test_raise_default_propagates() -> None:
    """Default :attr:`ErrorPolicy.RAISE` cancels the recorder on error.

    The error escapes through a task group, so it surfaces as a
    :class:`BaseExceptionGroup` carrying the wrapped :class:`NIDaqReadError`.
    """
    backend = FakeDaqBackend(
        read_errors={"ai_err": [NIDaqReadError("bad read")]},
    )
    with pytest.raises(BaseExceptionGroup) as ei:  # noqa: PT012
        async with await open_device(_make_spec(), backend=backend) as session:
            async with record(
                session,
                chunk_size=50,
                buffer_size=4,
                error_policy=ErrorPolicy.RAISE,
            ) as _rec2:
                rx, _summary = _rec2.stream, _rec2.summary
                async for _ in rx:
                    pass
    # The original NIDaqReadError must be in the group.
    matched, _rest = ei.value.split(NIDaqReadError)
    assert matched is not None
