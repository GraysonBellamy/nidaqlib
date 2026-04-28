"""Block E — :class:`DaqManager` lifecycle on real TC hardware.

We can validate the manager's lifecycle, refcount, fan-out, and resource
preflight surfaces with a TC module — outputs and the safety gates remain
out of scope (no AO / DI / DO on a TC card).

Each test starts with a clean :class:`DaqManager` and ends with an
``async with`` close that asserts every owned session has torn down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import anyio
import pytest

from nidaqlib import (
    DaqManager,
    DaqReading,
    ErrorPolicy,
    NIDaqError,
    NIDaqResourceError,
    TaskSpec,
    Timing,
    record_polled,
)

from .conftest import assert_plausible_temperature, make_tc_channel

if TYPE_CHECKING:
    from nidaqlib.manager import TaskResult

    from .conftest import TcHardwareConfig


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# E1 — single multi-channel task via the manager
# ---------------------------------------------------------------------------


async def test_e1_manager_single_task_read_block(
    tc_config: TcHardwareConfig,
    tc_spec_continuous_two_channel: TaskSpec,
) -> None:
    """``DaqManager`` runs one task end-to-end and produces a ``(2, N)`` block."""
    chunk = max(2, int(tc_config.rate_hz // 2))

    async with DaqManager() as mgr:
        await mgr.add("primary_pair", tc_spec_continuous_two_channel)
        await mgr.start()
        results = await mgr.read_block(chunk, ["primary_pair"])

    assert set(results) == {"primary_pair"}
    result = results["primary_pair"]
    assert result.ok
    block = result.value
    assert block is not None
    assert block.data.shape == (2, chunk)
    for row, label in enumerate(block.channels):
        assert_plausible_temperature(float(block.data[row, 0]), tc_config, where=f"E1.{label}")


# ---------------------------------------------------------------------------
# E2 — refcount: same name+spec twice; remove once → still alive
# ---------------------------------------------------------------------------


async def test_e2_refcount_holds_session_alive(
    tc_spec_on_demand: TaskSpec,
) -> None:
    """A duplicate ``add`` bumps refcount; one ``remove`` does not tear down."""
    async with DaqManager() as mgr:
        s1 = await mgr.add("ref_test", tc_spec_on_demand)
        s2 = await mgr.add("ref_test", tc_spec_on_demand)
        assert s1 is s2
        await mgr.remove("ref_test")
        # Still registered after one remove.
        assert "ref_test" in mgr.names
        # Last remove tears it down.
        await mgr.remove("ref_test")
        assert "ref_test" not in mgr.names


# ---------------------------------------------------------------------------
# E3 — physical-channel preflight catches obvious overlap
# ---------------------------------------------------------------------------


async def test_e3_preflight_rejects_overlapping_channel(
    tc_config: TcHardwareConfig,
    tc_spec_on_demand: TaskSpec,
) -> None:
    """Adding a second task that targets the same physical channel raises."""
    async with DaqManager() as mgr:
        await mgr.add("first", tc_spec_on_demand)
        # Build a second spec that reuses the same primary channel.
        clash_spec = TaskSpec(
            name="clash",
            channels=[make_tc_channel(tc_config, name="primary")],
        )
        with pytest.raises(NIDaqResourceError):
            await mgr.add("second", clash_spec)


# ---------------------------------------------------------------------------
# E4 — invalid spec under ErrorPolicy.RETURN surfaces TaskResult.error
# ---------------------------------------------------------------------------


async def test_e4_invalid_spec_returns_taskresult_error(
    tc_config: TcHardwareConfig,
    tc_spec_on_demand: TaskSpec,
) -> None:
    """A bogus device alias surfaces as ``TaskResult.error`` under ``RETURN``.

    Pairs one valid TC task with one task targeting an obviously invalid
    physical channel; the manager-level ``start`` under
    :attr:`ErrorPolicy.RETURN` MUST collect the failure into the
    per-task :class:`TaskResult` rather than raising.
    """
    bogus_spec = TaskSpec(
        name="bogus",
        channels=[
            make_tc_channel(
                tc_config,
                physical_channel="ThisDeviceDoesNotExist/ai0",
                name="ghost",
            )
        ],
        timing=Timing(rate_hz=tc_config.rate_hz),
    )

    async with DaqManager(error_policy=ErrorPolicy.RETURN) as mgr:
        await mgr.add("ok", tc_spec_on_demand)
        await mgr.add("bad", bogus_spec)
        results = await mgr.start()

        ok_result = results["ok"]
        bad_result = results["bad"]
        assert ok_result.ok, f"valid task failed unexpectedly: {ok_result.error!r}"
        assert not bad_result.ok
        assert isinstance(bad_result.error, NIDaqError)
        # Other tasks remain operable: poll the valid one.
        polls = await mgr.poll(["ok"])
        ok_poll = polls["ok"]
        assert ok_poll.ok
        assert ok_poll.value is not None


# ---------------------------------------------------------------------------
# E5 — record_polled fan-out across two single-channel tasks (best-effort)
# ---------------------------------------------------------------------------


async def test_e5_record_polled_manager_fanout(
    tc_config: TcHardwareConfig,
) -> None:
    """``record_polled(manager, ...)`` emits a per-task ``TaskResult`` mapping.

    Uses two **single-channel on-demand** tasks. Some TC modules treat the
    whole module as one resource and reject a second concurrent task — if
    that happens, the test is xfailed dynamically with the NI error code.
    """
    if tc_config.channel_secondary is None:
        pytest.skip("two single-channel manager tasks need a secondary channel")

    spec_a = TaskSpec(
        name="poll_a",
        channels=[make_tc_channel(tc_config, name="ch_a")],
    )
    spec_b = TaskSpec(
        name="poll_b",
        channels=[
            make_tc_channel(
                tc_config,
                physical_channel=tc_config.channel_secondary,
                name="ch_b",
            )
        ],
    )

    rate_hz = 4.0
    duration_s = 2.0

    async with DaqManager() as mgr:
        await mgr.add("poll_a", spec_a)
        await mgr.add("poll_b", spec_b)
        try:
            await mgr.start()
        except BaseExceptionGroup as group:
            pytest.xfail(
                f"NI rejected two concurrent tasks on the same TC module: {group.exceptions!r}"
            )

        ticks: list[dict[str, TaskResult[DaqReading]]] = []
        async with record_polled(mgr, rate_hz=rate_hz, buffer_size=8) as (rx, summary):
            deadline = anyio.current_time() + duration_s
            async for payload in rx:
                # Manager mode emits a Mapping[str, TaskResult[DaqReading]].
                tick = cast(
                    "dict[str, TaskResult[DaqReading]]",
                    dict(payload),  # type: ignore[arg-type]
                )
                ticks.append(tick)
                if anyio.current_time() >= deadline:
                    break

    assert ticks, "manager fan-out emitted no ticks"
    for tick in ticks:
        assert set(tick) == {"poll_a", "poll_b"}
        for name, result in tick.items():
            assert result.ok, f"{name}: {result.error!r}"
            reading = cast("DaqReading", result.value)
            for ch, value in reading.values.items():
                assert_plausible_temperature(float(value), tc_config, where=f"E5.{name}.{ch}")
    assert summary.errors_observed == 0
