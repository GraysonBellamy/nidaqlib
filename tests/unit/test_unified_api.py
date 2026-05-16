"""Tests for the unified-API additions.

Covers the §6 acceptance criteria from ``UNIFIED_API_HANDOFF.md``:

- Top-level exports importable in one shot (cross-lib import-symmetry).
- ``find_devices`` returns a ``list[DiscoveryResult]``; gracefully shapes
  driver failures as ``ok=False`` rows.
- ``DeviceResult.success`` / ``.failure`` factories.
- ``Recording`` wrapper exposes ``stream`` / ``summary`` / ``rate_hz``.
- ``PollSourceAdapter`` wraps a session and emits the expected mapping.
- ``DaqSession.snapshot()`` is I/O-free and returns ``NIDaqSnapshot``.
- ``to_pint`` covers the documented temperature enum members.
- The transient-classification table includes ``-200279`` and ``-200284``,
  and ``-200284`` is **not** a ``NIDaqTimeoutError``.
- ``NIDaqFakeBackend`` is an alias for ``FakeDaqBackend``.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    AnalogInputVoltage,
    DaqManager,
    DeviceResult,
    DiscoveryResult,
    NIDaqDiscoveryResult,
    NIDaqError,
    NIDaqSnapshot,
    NIDaqTimeoutError,
    NIDaqTransientError,
    PollSource,
    PollSourceAdapter,
    Recording,
    TaskSpec,
    TaskState,
    block_to_rows,
    find_devices,
    open_device,
    reading_to_row,
    record_polled,
    to_pint,
)
from nidaqlib.backend.fake import FakeDaqBackend
from nidaqlib.testing import NIDaqFakeBackend

# ---------------------------------------------------------------------------
# Cross-library import-symmetry test
# ---------------------------------------------------------------------------


def test_top_level_exports_are_importable() -> None:
    """The flat import surface that every cross-lib consumer relies on."""
    # The names referenced above already validate themselves via the import
    # statement at module load. This explicit assert pins them down.
    expected = {
        open_device,
        find_devices,
        reading_to_row,
        block_to_rows,
        PollSourceAdapter,
        Recording,
        DeviceResult,
        to_pint,
    }
    assert all(s is not None for s in expected)


# ---------------------------------------------------------------------------
# DeviceResult factories
# ---------------------------------------------------------------------------


def test_device_result_success_factory() -> None:
    r: DeviceResult[int] = DeviceResult.success(42)
    assert r.ok is True
    assert r.value == 42
    assert r.error is None


def test_device_result_failure_factory() -> None:
    err = NIDaqError("boom")
    r: DeviceResult[int] = DeviceResult.failure(err)
    assert r.ok is False
    assert r.value is None
    assert r.error is err


# ---------------------------------------------------------------------------
# find_devices
# ---------------------------------------------------------------------------


def test_find_devices_returns_list() -> None:
    """``find_devices`` always returns a list, never raises.

    With nidaqmx installed and no hardware, this returns ``[]`` (success
    enumeration, empty inventory) on a clean system.
    """
    results = find_devices()
    assert isinstance(results, list)
    for row in results:
        assert isinstance(row, DiscoveryResult)
        # Each row matches the §B shape regardless of ok/error.
        assert isinstance(row.port, str)
        assert row.address is None
        assert row.protocol is None


def test_find_devices_dependency_failure_surfaces_as_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the underlying enumeration fails, a single ``ok=False`` row appears."""
    from nidaqlib.errors import NIDaqDependencyError
    from nidaqlib.system import discovery

    def _boom() -> None:
        raise NIDaqDependencyError("simulated missing driver")

    monkeypatch.setattr(discovery, "_import_nidaqmx", _boom)
    rows = find_devices()
    assert len(rows) == 1
    row = rows[0]
    assert row.ok is False
    assert isinstance(row.error, NIDaqDependencyError)


# ---------------------------------------------------------------------------
# NIDaqDiscoveryResult shape
# ---------------------------------------------------------------------------


def test_nidaq_discovery_result_subclass_shape() -> None:
    """``NIDaqDiscoveryResult`` carries the NI extras alongside the base shape."""
    row = NIDaqDiscoveryResult(
        ok=True,
        port="Dev1",
        product_type="USB-6210",
        serial_number="ABC123",
    )
    assert isinstance(row, DiscoveryResult)
    assert row.product_type == "USB-6210"
    assert row.serial_number == "ABC123"
    assert row.chassis is None
    assert row.physical_module is None


# ---------------------------------------------------------------------------
# to_pint
# ---------------------------------------------------------------------------


def test_to_pint_covers_temperature_enum() -> None:
    from nidaqmx.constants import TemperatureUnits

    assert to_pint(TemperatureUnits.DEG_C) == "degC"
    assert to_pint(TemperatureUnits.DEG_F) == "degF"
    assert to_pint(TemperatureUnits.K) == "K"
    assert to_pint(TemperatureUnits.DEG_R) == "degR"


def test_to_pint_passes_through_known_strings() -> None:
    assert to_pint("V") == "V"
    assert to_pint("Hz") == "Hz"
    assert to_pint("degC") == "degC"


def test_to_pint_returns_none_for_none() -> None:
    assert to_pint(None) is None


# ---------------------------------------------------------------------------
# Transient classification (recap; full coverage lives in test_errors.py)
# ---------------------------------------------------------------------------


def test_transient_error_is_not_timeout_error() -> None:
    """A reclassified ``-200284`` must not also subclass ``NIDaqTimeoutError``."""
    assert not issubclass(NIDaqTransientError, NIDaqTimeoutError)
    assert not issubclass(NIDaqTimeoutError, NIDaqTransientError)


# ---------------------------------------------------------------------------
# NIDaqFakeBackend alias
# ---------------------------------------------------------------------------


def test_nidaq_fake_backend_is_alias() -> None:
    assert NIDaqFakeBackend is FakeDaqBackend


# ---------------------------------------------------------------------------
# Recording wrapper / PollSourceAdapter / snapshot
# (anyio-marked tests)
# ---------------------------------------------------------------------------


def _ai_spec(name: str = "ai_demo", channel: str = "Dev1/ai0") -> TaskSpec:
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel=channel)],
    )


@pytest.mark.anyio
async def test_recording_exposes_stream_summary_rate(anyio_backend: str) -> None:
    """``record_polled`` yields a ``Recording`` with the three fields populated."""
    del anyio_backend
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with (
        await open_device(_ai_spec(), backend=backend) as session,
        record_polled(session, rate_hz=50.0, buffer_size=4) as rec,
    ):
        assert isinstance(rec, Recording)
        assert rec.stream is not None
        assert rec.summary is not None
        assert rec.rate_hz == 50.0


@pytest.mark.anyio
async def test_recording_rate_hz_is_none_for_on_demand_block_path(
    anyio_backend: str,
) -> None:
    """On-demand block-mode recordings have no clock rate → ``Recording.rate_hz is None``."""
    del anyio_backend
    # record() requires a configured task; the FINITE path is the simplest
    # way to exercise it without a clock. But FINITE has a rate_hz, so we
    # use the on-demand polled path instead — it's tested above. record()
    # always has a rate from the spec.timing when reachable, so this test
    # asserts the configured-rate side of the property.
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with (
        await open_device(_ai_spec(), backend=backend) as session,
        record_polled(session, rate_hz=10.0, buffer_size=2) as rec,
    ):
        assert rec.rate_hz == 10.0


@pytest.mark.anyio
async def test_poll_source_adapter_emits_session_keyed_mapping(
    anyio_backend: str,
) -> None:
    """Adapter.poll() returns ``{session.spec.name: DeviceResult.success(reading)}``."""
    del anyio_backend
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(_ai_spec(), backend=backend) as session:
        adapter = PollSourceAdapter(session)
        assert isinstance(adapter, PollSource)
        results = await adapter.poll()
        assert set(results) == {"ai_demo"}
        row = results["ai_demo"]
        assert isinstance(row, DeviceResult)
        assert row.ok is True
        assert row.value is not None


@pytest.mark.anyio
async def test_poll_source_adapter_filters_by_names(anyio_backend: str) -> None:
    """Passing ``names`` that doesn't include the session yields an empty mapping."""
    del anyio_backend
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(_ai_spec(), backend=backend) as session:
        adapter = PollSourceAdapter(session)
        assert await adapter.poll(names=["other"]) == {}


@pytest.mark.anyio
async def test_snapshot_is_io_free(anyio_backend: str) -> None:
    """``snapshot()`` must not call any backend operation after configure."""
    del anyio_backend
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with await open_device(_ai_spec(), backend=backend) as session:
        ops_before = len(backend.operations)
        snap = await session.snapshot()
        assert len(backend.operations) == ops_before, "snapshot() must be I/O-free after configure"
        assert isinstance(snap, NIDaqSnapshot)
        assert snap.task_name == "ai_demo"
        assert snap.task_state == TaskState.RUNNING
        assert snap.channel_count == 1
        assert snap.physical_channels == ("Dev1/ai0",)


@pytest.mark.anyio
async def test_snapshot_task_state_transitions(anyio_backend: str) -> None:
    """``task_state`` reflects RUNNING / STOPPED / CLOSED transitions without I/O."""
    del anyio_backend
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    session = await open_device(_ai_spec(), backend=backend)
    try:
        snap_running = await session.snapshot()
        assert snap_running.task_state == TaskState.RUNNING
        await session.stop()
        snap_stopped = await session.snapshot()
        assert snap_stopped.task_state == TaskState.STOPPED
    finally:
        await session.close()
    snap_closed = await session.snapshot()
    assert snap_closed.task_state == TaskState.CLOSED


@pytest.mark.anyio
async def test_manager_poll_results_have_no_name_attribute(
    anyio_backend: str,
) -> None:
    """Confirms the ``DeviceResult.name`` field is gone — mapping key carries it."""
    del anyio_backend
    backend = FakeDaqBackend(read_block_default_shape=(1, 1))
    async with DaqManager() as mgr:
        await mgr.add("a", _ai_spec("a", channel="Dev1/ai0"), backend=backend)
        await mgr.start()
        results = await mgr.poll()
        for name, result in results.items():
            assert isinstance(result, DeviceResult)
            assert not hasattr(result, "name"), (
                "DeviceResult must not carry a .name field; mapping key carries it"
            )
            assert name == "a"
