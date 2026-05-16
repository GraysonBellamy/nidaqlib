"""Block A — :class:`DaqSession` lifecycle on real TC hardware.

Covers ``open_device``, ``read_block``, ``raw_task``, and the ``acquire``
finite-mode helper with :class:`ThermocoupleInput`, the AI variant accepted by
TC-only modules.

Each test runs under a fresh ``open_device`` context so a failure in one
test does not leak NI resources into the next.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest

from nidaqlib import (
    DaqBlock,
    DaqReading,
    TaskSpec,
    open_device,
)

from .conftest import assert_close_float, assert_plausible_temperature

if TYPE_CHECKING:
    from collections.abc import Sized

    from .conftest import TcHardwareConfig


pytestmark = pytest.mark.anyio


class _NidaqmxTaskProbe(Protocol):
    name: str
    ai_channels: Sized


# A1 ------------------------------------------------------------------------


async def test_a1_poll_returns_reading(
    tc_config: TcHardwareConfig,
    tc_spec_on_demand: TaskSpec,
) -> None:
    """``open_device`` + ``poll()`` returns one :class:`DaqReading` with a TC value."""
    async with await open_device(tc_spec_on_demand) as session:
        reading = await session.poll()

    assert isinstance(reading, DaqReading)
    assert reading.device == "tc_on_demand"
    assert "primary" in reading.values
    value = reading.values["primary"]
    assert isinstance(value, float)
    assert_plausible_temperature(value, tc_config, where="A1.poll")
    # Provenance fields are populated.
    assert reading.latency_s >= 0.0
    assert reading.t_mono_ns > 0
    assert reading.t_utc == reading.requested_at + (reading.received_at - reading.requested_at) / 2


# A2 ------------------------------------------------------------------------


async def test_a2_acquire_finite_block(
    tc_config: TcHardwareConfig,
    tc_spec_finite: TaskSpec,
) -> None:
    """FINITE-mode ``acquire(N)`` returns one ``(1, N)`` :class:`DaqBlock`."""
    samples_per_channel = tc_spec_finite.timing.samples_per_channel  # type: ignore[union-attr]
    assert samples_per_channel is not None  # fixture invariant

    async with await open_device(tc_spec_finite) as session:
        block = await session.acquire(samples_per_channel)
        # ``acquire`` stops the task after the read — confirm.
        assert session.is_started is False

    assert isinstance(block, DaqBlock)
    assert block.channels == ("primary",)
    assert block.data.shape == (1, samples_per_channel)
    assert block.block_index == 0
    assert block.first_sample_index == 0
    assert block.samples_per_channel == samples_per_channel
    assert_close_float(block.sample_rate_hz, tc_config.rate_hz, where="A2.sample_rate_hz")
    assert block.block_period_ns is not None
    assert_close_float(
        block.block_period_ns / 1e9, 1.0 / tc_config.rate_hz, where="A2.block_period_ns"
    )
    # Spot-check a representative sample.
    assert_plausible_temperature(float(block.data[0, 0]), tc_config, where="A2.first_sample")
    assert_plausible_temperature(float(block.data[0, -1]), tc_config, where="A2.last_sample")


# A3 ------------------------------------------------------------------------


async def test_a3_continuous_read_block_advances_counters(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
) -> None:
    """Five sequential ``read_block`` calls produce monotonic per-task indices."""
    chunk = max(2, int(tc_config.rate_hz // 2))  # ~0.5 s per block
    n_blocks = 5

    async with await open_device(tc_spec_continuous) as session:
        blocks: list[DaqBlock] = [await session.read_block(chunk) for _ in range(n_blocks)]

    # block_index monotonic from 0
    assert [b.block_index for b in blocks] == list(range(n_blocks))
    # first_sample_index advances by chunk
    assert [b.first_sample_index for b in blocks] == [i * chunk for i in range(n_blocks)]
    # task_started_at is the same anchor across the run
    anchors = {b.task_started_at for b in blocks}
    assert len(anchors) == 1, f"task_started_at drifted across blocks: {anchors!r}"
    # Shape invariants hold for every block
    for b in blocks:
        assert b.data.shape == (1, chunk)
        assert b.block_period_ns is not None
        assert_close_float(
            b.block_period_ns / 1e9, 1.0 / tc_config.rate_hz, where="A3.block_period_ns"
        )


# A4 ------------------------------------------------------------------------


async def test_a4_raw_task_escape_hatch(tc_spec_continuous: TaskSpec) -> None:
    """``session.raw_task`` returns the underlying ``nidaqmx.Task``.

    Smoke-tests the escape hatch from design doc §7.4: once started, the
    raw NI task is reachable and can be inspected (channel count, name).
    """
    import nidaqmx

    async with await open_device(tc_spec_continuous) as session:
        # Touch raw_task before the first read — verifies the property is
        # populated as soon as ``start`` returns, not lazily on first read.
        raw = session.raw_task
        assert isinstance(raw, nidaqmx.Task)
        raw_probe = cast("_NidaqmxTaskProbe", raw)
        assert raw_probe.name == tc_spec_continuous.name
        # Sanity: the channel count matches the spec.
        assert len(raw_probe.ai_channels) == len(tc_spec_continuous.channels)


# A5 ------------------------------------------------------------------------


async def test_a5_two_channel_poll(
    tc_config: TcHardwareConfig,
    tc_spec_continuous_two_channel: TaskSpec,
) -> None:
    """Two-channel TC task: a single ``read_block`` returns ``(2, N)`` data.

    ``poll()`` cannot be used here because the spec is CONTINUOUS — we use
    ``read_block`` instead, which is the live-scalar use-case from
    design doc §9.2.
    """
    chunk = max(2, int(tc_config.rate_hz // 2))

    async with await open_device(tc_spec_continuous_two_channel) as session:
        block = await session.read_block(chunk)

    assert block.channels == ("primary", "secondary")
    assert block.data.shape == (2, chunk)
    for row, label in enumerate(block.channels):
        assert_plausible_temperature(
            float(block.data[row, 0]),
            tc_config,
            where=f"A5.{label}.first_sample",
        )


# A6 — quick sanity: an on-demand spec also rejects a bad mode --------------


async def test_a6_poll_rejected_for_continuous_task(
    tc_spec_continuous: TaskSpec,
) -> None:
    """Defensive check: ``poll()`` on a started CONTINUOUS task raises.

    Mirrors the unit-test check, but exercising the *real* lifecycle on
    hardware confirms the guard fires before NI even sees the request.
    """
    from nidaqlib import NIDaqTaskStateError

    async with await open_device(tc_spec_continuous) as session:
        with pytest.raises(NIDaqTaskStateError):
            await session.poll()


# A7 — stop / restart cycle on the same session ----------------------------


async def test_a7_stop_then_restart_same_session(
    tc_spec_continuous: TaskSpec,
) -> None:
    """``stop()`` + ``start()`` on the same session resumes acquisition.

    Verifies the lifecycle code path that lets a caller pause acquisition
    without tearing down the session. NI permits ``task.start()`` after a
    prior ``stop()`` without a re-configure; this test confirms our
    wrapper preserves that.

    On real hardware:

    - ``read_block`` works after the second ``start``.
    - ``task_started_at`` advances — the new run gets a fresh anchor (we
      assume the second start is not silently reusing the first anchor).
    """
    chunk = 4
    async with await open_device(tc_spec_continuous) as session:
        first = await session.read_block(chunk)
        first_anchor = session.task_started_at
        assert first_anchor is not None

        await session.stop()
        assert not session.is_started

        await session.start()
        assert session.is_started
        second_anchor = session.task_started_at  # type: ignore[unreachable]
        assert second_anchor is not None
        # Second anchor must be >= first; samples in the second run start
        # over at sample_index 0 since NI re-arms the clock on restart.
        assert second_anchor >= first_anchor

        second = await session.read_block(chunk)
        assert second.data.shape == first.data.shape


# A8 — invalid sample rate is rejected with a clear error -------------------


async def test_a8_invalid_sample_rate_rejected(
    tc_config: TcHardwareConfig,
) -> None:
    """A rate well above the module's max surfaces a typed NI error.

    The 9214's hardware max is around 75 S/s (cold-junction, high-resolution
    mode). A 100 kHz request must fail at ``start()`` with
    :class:`NIDaqBackendError` carrying NI's code, not a silent reduction
    or a generic exception. Confirms the configuration error path is wired
    correctly through the wrapper.
    """
    from nidaqlib import AcquisitionMode, NIDaqError, Timing

    from .conftest import make_tc_channel

    spec = TaskSpec(
        name="tc_too_fast",
        channels=[make_tc_channel(tc_config, name="primary")],
        timing=Timing(rate_hz=100_000.0, mode=AcquisitionMode.CONTINUOUS),
    )
    with pytest.raises(NIDaqError) as exc_info:
        async with await open_device(spec):
            pass
    # The NI error code must be present on the wrapper context — that's
    # what an operator inspecting the failure will see in their logs.
    ctx = exc_info.value.context
    assert ctx is not None
    assert ctx.ni_error_code is not None, f"expected NI error_code on context, got {ctx!r}"
