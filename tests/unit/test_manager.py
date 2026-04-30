"""Tests for :class:`DaqManager` (design doc §15) and :class:`DeviceResult`.

Covers add/remove ref-counting, LIFO close, group operations
(`start`/`stop`/`poll`/`read_block`), `ExceptionGroup` semantics on
RAISE policy, `RETURN` per-task error rows, and the discovery-driven
preflight conflict detection.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DaqManager,
    DeviceResult,
    ErrorPolicy,
    NIDaqReadError,
    NIDaqResourceError,
    NIDaqTaskStateError,
    TaskSpec,
    Timing,
)
from nidaqlib.backend import FakeDaqBackend


def _ai_spec(
    name: str,
    *,
    channel: str = "Dev1/ai0",
    mode: AcquisitionMode | None = None,
) -> TaskSpec:
    timing = Timing(rate_hz=1000.0, mode=mode) if mode is not None else None
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel=channel, name=f"{name}_ch", unit="V")],
        timing=timing,
    )


@pytest.mark.anyio
async def test_add_then_get_returns_session() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    async with DaqManager() as mgr:
        session = await mgr.add("a", _ai_spec("a"), backend=backend)
        assert mgr.get("a") is session


@pytest.mark.anyio
async def test_add_idempotent_refcount() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with DaqManager() as mgr:
        s1 = await mgr.add("a", _ai_spec("a"), backend=backend)
        s2 = await mgr.add("a", _ai_spec("a"), backend=backend)
        assert s1 is s2
        await mgr.remove("a")
        # First remove only decrements the refcount.
        assert "a" in mgr.names
        await mgr.remove("a")
        assert "a" not in mgr.names


@pytest.mark.anyio
async def test_add_rejects_conflict_on_same_physical_channel() -> None:
    backend = FakeDaqBackend()
    async with DaqManager() as mgr:
        await mgr.add("a", _ai_spec("a", channel="Dev1/ai0"), backend=backend)
        with pytest.raises(NIDaqResourceError) as excinfo:
            await mgr.add("b", _ai_spec("b", channel="Dev1/ai0"), backend=backend)
        # Conflict carries the offending task name.
        conflicts = excinfo.value.context.extra.get("conflicts", {})
        assert "a" in conflicts


@pytest.mark.anyio
async def test_add_unique_channels_no_conflict() -> None:
    backend = FakeDaqBackend()
    async with DaqManager() as mgr:
        await mgr.add("a", _ai_spec("a", channel="Dev1/ai0"), backend=backend)
        await mgr.add("b", _ai_spec("b", channel="Dev1/ai1"), backend=backend)
        assert set(mgr.names) == {"a", "b"}


@pytest.mark.anyio
async def test_add_module_level_preflight_rejects_tc_module_share() -> None:
    """A second task on a TC-class module is rejected at preflight.

    Locks the §15.3 module-reservation guard against the fake backend so a
    regression doesn't have to wait for hardware day. Uses
    ``FakeDaqBackend.register_device_info`` to script the product type
    that the preflight queries via ``backend.device_info``.
    """
    backend = FakeDaqBackend()
    backend.register_device_info("cDAQ1Mod1", product_type="NI 9214")
    async with DaqManager() as mgr:
        await mgr.add(
            "first",
            _ai_spec("first", channel="cDAQ1Mod1/ai0"),
            backend=backend,
        )
        with pytest.raises(NIDaqResourceError, match="module-level reservation"):
            await mgr.add(
                "second",
                _ai_spec("second", channel="cDAQ1Mod1/ai1"),
                backend=backend,
            )


@pytest.mark.anyio
async def test_add_module_level_preflight_skips_unknown_product() -> None:
    """A device with an unknown / non-reserved product type allows two tasks.

    Confirms the module-level guard is a *narrow* allow-list — only the
    products in :data:`_MODULE_RESERVED_PRODUCTS` (TC modules) trigger
    rejection. Generic AI modules / unknown product types still go
    through to NI for runtime adjudication.
    """
    backend = FakeDaqBackend()
    # USB-6001 is a multifunction AI module that does NOT reserve the
    # whole module per task. Even though the fake doesn't enforce NI's
    # actual reservation behaviour, the preflight should not raise.
    backend.register_device_info("Dev1", product_type="USB-6001")
    async with DaqManager() as mgr:
        await mgr.add("a", _ai_spec("a", channel="Dev1/ai0"), backend=backend)
        await mgr.add("b", _ai_spec("b", channel="Dev1/ai1"), backend=backend)
        assert set(mgr.names) == {"a", "b"}


@pytest.mark.anyio
async def test_add_module_level_preflight_handles_unknown_device() -> None:
    """When ``device_info`` returns ``None``, the module-level check is skipped.

    Production behaviour: an unknown device alias means ``backend.device_info``
    returns ``None`` (NI would otherwise reject downstream at start time).
    The preflight must not raise on that path — the module-level check
    only fires when we have a positively-identified TC-class product.
    """
    backend = FakeDaqBackend()  # no device info registered
    async with DaqManager() as mgr:
        await mgr.add("a", _ai_spec("a", channel="Dev1/ai0"), backend=backend)
        # Same device, different channel — preflight should fall through
        # because the fake returned None for device_info.
        await mgr.add("b", _ai_spec("b", channel="Dev1/ai1"), backend=backend)
        assert set(mgr.names) == {"a", "b"}


@pytest.mark.anyio
async def test_start_and_read_block_fanout() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 32))
    async with DaqManager() as mgr:
        await mgr.add(
            "a",
            _ai_spec("a", channel="Dev1/ai0", mode=AcquisitionMode.CONTINUOUS),
            backend=backend,
        )
        await mgr.add(
            "b",
            _ai_spec("b", channel="Dev1/ai1", mode=AcquisitionMode.CONTINUOUS),
            backend=backend,
        )
        await mgr.start()
        results = await mgr.read_block(32)
        assert set(results) == {"a", "b"}
        for name, result in results.items():
            assert isinstance(result, DeviceResult)
            assert result.ok is True
            assert result.value is not None
            assert result.value.samples_per_channel == 32
            assert result.name == name


@pytest.mark.anyio
async def test_poll_fanout_on_demand() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with DaqManager() as mgr:
        await mgr.add(
            "a", _ai_spec("a", channel="Dev1/ai0", mode=AcquisitionMode.ON_DEMAND), backend=backend
        )
        await mgr.add(
            "b", _ai_spec("b", channel="Dev1/ai1", mode=AcquisitionMode.ON_DEMAND), backend=backend
        )
        await mgr.start()
        results = await mgr.poll()
        assert set(results) == {"a", "b"}
        for r in results.values():
            assert r.error is None


@pytest.mark.anyio
async def test_raise_policy_groups_errors() -> None:
    """Per-task errors under RAISE policy collect into an ExceptionGroup."""
    err_a = NIDaqReadError("a-broken")
    backend = FakeDaqBackend(
        read_block_default_shape=(1, 8),
        read_errors={"a": [err_a]},
    )
    async with DaqManager(error_policy=ErrorPolicy.RAISE) as mgr:
        await mgr.add(
            "a", _ai_spec("a", channel="Dev1/ai0", mode=AcquisitionMode.CONTINUOUS), backend=backend
        )
        await mgr.add(
            "b", _ai_spec("b", channel="Dev1/ai1", mode=AcquisitionMode.CONTINUOUS), backend=backend
        )
        await mgr.start()
        with pytest.raises(BaseExceptionGroup) as excinfo:
            await mgr.read_block(8)
        # The group carries the specific NIDaqReadError.
        assert any(isinstance(e, NIDaqReadError) for e in excinfo.value.exceptions)


@pytest.mark.anyio
async def test_return_policy_yields_error_rows() -> None:
    err_a = NIDaqReadError("a-broken")
    backend = FakeDaqBackend(
        read_block_default_shape=(1, 8),
        read_errors={"a": [err_a]},
    )
    async with DaqManager(error_policy=ErrorPolicy.RETURN) as mgr:
        await mgr.add(
            "a", _ai_spec("a", channel="Dev1/ai0", mode=AcquisitionMode.CONTINUOUS), backend=backend
        )
        await mgr.add(
            "b", _ai_spec("b", channel="Dev1/ai1", mode=AcquisitionMode.CONTINUOUS), backend=backend
        )
        await mgr.start()
        results = await mgr.read_block(8)
        assert results["a"].error is err_a
        assert results["a"].value is None
        assert results["b"].ok


@pytest.mark.anyio
async def test_close_lifo_order() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    mgr = DaqManager()
    await mgr.add("a", _ai_spec("a", channel="Dev1/ai0"), backend=backend)
    await mgr.add("b", _ai_spec("b", channel="Dev1/ai1"), backend=backend)
    await mgr.add("c", _ai_spec("c", channel="Dev1/ai2"), backend=backend)
    await mgr.start()
    await mgr.close()
    # The FakeDaqBackend records create/close/etc. operations in call order;
    # close must fire in LIFO (c, b, a).
    close_order = [op.task_name for op in backend.operations if op.op == "close_task"]
    assert close_order == ["c", "b", "a"]


@pytest.mark.anyio
async def test_close_idempotent_and_blocks_further_add() -> None:
    backend = FakeDaqBackend()
    mgr = DaqManager()
    await mgr.add("a", _ai_spec("a"), backend=backend)
    await mgr.close()
    assert mgr.is_closed
    # Idempotent.
    await mgr.close()
    with pytest.raises(NIDaqTaskStateError):
        await mgr.add("b", _ai_spec("b"), backend=backend)
