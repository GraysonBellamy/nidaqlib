"""Block E — :class:`DaqManager` lifecycle on real TC hardware.

We can validate the manager's lifecycle, refcount, fan-out, and resource
preflight surfaces with a TC module — outputs and the safety gates remain
out of scope (no AO / DI / DO on a TC card).

Each test starts with a clean :class:`DaqManager` and ends with an
``async with`` close that asserts every owned session has torn down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from nidaqlib import (
    DaqManager,
    ErrorPolicy,
    NIDaqError,
    NIDaqResourceError,
    TaskSpec,
    Timing,
)

from .conftest import assert_plausible_temperature, make_tc_channel

if TYPE_CHECKING:
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
# E5 — module-level reservation preflight on TC modules (NI 9211/9212/9213/9214)
# ---------------------------------------------------------------------------


async def test_e5_module_reservation_preflight_on_tc_module(
    tc_config: TcHardwareConfig,
) -> None:
    """The manager's preflight rejects two tasks on a whole-module-reserved device.

    NI 9211/9212/9213/9214 reserve the whole module per task — a second
    concurrent task targeting any AI channel on the same module is
    rejected by NI at ``start()`` time with -50103. The manager's
    preflight queries ``backend.device_info`` on the first ``add()``,
    notices the product type is in the module-reservation set, and raises
    :class:`NIDaqResourceError` at the **second** ``add()``. The error
    message names the offending device(s) and references §15.3.

    Skips if the operator's hardware is not in the known TC-reservation set
    — there's nothing to assert on a non-TC module.
    """
    if tc_config.channel_secondary is None:
        pytest.skip("two single-channel manager tasks need a secondary channel")

    from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend
    from nidaqlib.manager import _is_module_reserved_product  # pyright: ignore[reportPrivateUsage]

    backend = NidaqmxBackend()
    info = backend.device_info(tc_config.device)
    if info is None or not _is_module_reserved_product(info.product_type):  # pyright: ignore[reportPrivateUsage]
        pytest.skip(
            f"device {tc_config.device!r} (product_type={info.product_type if info else None!r}) "
            f"is not in the module-reservation set; preflight test does not apply"
        )

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

    async with DaqManager() as mgr:
        await mgr.add("poll_a", spec_a)
        with pytest.raises(NIDaqResourceError, match="module-level reservation"):
            await mgr.add("poll_b", spec_b)
