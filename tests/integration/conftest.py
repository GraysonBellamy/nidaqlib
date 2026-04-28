"""Hardware-gated integration test fixtures.

Every test under ``tests/integration/`` is opt-in: the module is collected
only when both the NI driver is reachable AND the user has explicitly
enabled hardware tests with::

    export NIDAQLIB_ENABLE_HARDWARE_TESTS=1

For a thermocouple-only module (NI-9211 / 9212 / 9213 / 9214 / USB-9211)
the env-driven configuration looks like::

    export NIDAQLIB_ENABLE_HARDWARE_TESTS=1
    export NIDAQLIB_TEST_TC_DEVICE=cDAQ1Mod1
    export NIDAQLIB_TEST_TC_CHANNEL_PRIMARY=cDAQ1Mod1/ai0
    export NIDAQLIB_TEST_TC_CHANNEL_SECONDARY=cDAQ1Mod1/ai1   # optional
    export NIDAQLIB_TEST_TC_TYPE=K                            # default K
    export NIDAQLIB_TEST_TC_RATE_HZ=10                        # default 10
    export NIDAQLIB_TEST_TC_MIN_DEGC=-50                      # default -50
    export NIDAQLIB_TEST_TC_MAX_DEGC=200                      # default 200

If ``NIDAQLIB_TEST_TC_CHANNEL_PRIMARY`` is unset the conftest synthesises
a default of ``f"{device}/ai0"`` from ``NIDAQLIB_TEST_TC_DEVICE``. Tests
that need a second channel skip cleanly if
``NIDAQLIB_TEST_TC_CHANNEL_SECONDARY`` is unset.

Anyio backend selection is intentionally limited to ``asyncio`` here —
hardware tests don't need to verify scheduler portability (the unit suite
already does that), and running each hardware test under three backends
just multiplies the time on the bench.
"""

from __future__ import annotations

import importlib.util
import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from nidaqlib import (
    AnalogInputVoltage,
    TaskSpec,
    ThermocoupleInput,
    Timing,
)

if TYPE_CHECKING:
    from pathlib import Path

    from nidaqlib.channels.base import ChannelSpec


# ---------------------------------------------------------------------------
# Anyio backend — single-track for the hardware suite
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> object:
    """Run hardware tests on plain asyncio.

    Overrides the unit-suite fixture in ``tests/conftest.py`` (which
    parametrises across asyncio / asyncio+uvloop / trio). Hardware tests
    don't need scheduler-matrix coverage — they need to run once, fast.
    """
    return "asyncio"


# ---------------------------------------------------------------------------
# Hardware enablement & env config
# ---------------------------------------------------------------------------


_HARDWARE_ENV = "NIDAQLIB_ENABLE_HARDWARE_TESTS"


def _hardware_enabled() -> bool:
    """``True`` when the operator has opted in via the env gate."""
    return os.environ.get(_HARDWARE_ENV, "").strip() not in ("", "0", "false", "False")


def _nidaqmx_importable() -> bool:
    """``True`` when ``nidaqmx`` resolves at all (driver may still be absent)."""
    return importlib.util.find_spec("nidaqmx") is not None


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip every hardware test unless the env gate is set.

    Items are still collected so a plain ``pytest --collect-only`` shows
    them; only the actual call is skipped.
    """
    del config
    if _hardware_enabled() and _nidaqmx_importable():
        return
    reason = (
        f"hardware tests disabled — set {_HARDWARE_ENV}=1 and install nidaqmx "
        "with a connected NI device to enable"
    )
    skip_marker = pytest.mark.skip(reason=reason)
    for item in items:
        # Only mark items rooted under tests/integration/ — leaves the
        # unit suite untouched even if conftest discovery picks it up.
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Env-driven test config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TcHardwareConfig:
    """Resolved hardware configuration for the TC integration suite.

    Attributes:
        device: NI device alias (``"cDAQ1Mod1"``, ``"Dev1"``, ...).
        channel_primary: Physical channel string for the always-required
            primary TC, e.g. ``"cDAQ1Mod1/ai0"``.
        channel_secondary: Optional second TC physical channel; tests that
            need two channels skip when this is ``None``.
        tc_type: One of ``"J" | "K" | "T" | "E" | "N" | "B" | "R" | "S"``.
        rate_hz: Sample-clock rate for hardware-clocked tests. Cap chosen
            to fit even the slowest TC modules (NI-9211 maxes at ~14 S/s;
            NI-9213 at ~75 S/s in high-speed mode).
        min_degc: Lower temperature limit for the channel range.
        max_degc: Upper temperature limit for the channel range.
    """

    device: str
    channel_primary: str
    channel_secondary: str | None
    tc_type: str
    rate_hz: float
    min_degc: float
    max_degc: float


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} not set; required for this test")
    return value


def _optional_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        pytest.skip(f"{name}={raw!r} is not a float")


@pytest.fixture(scope="session")
def tc_config() -> TcHardwareConfig:
    """Resolve hardware config from env. Skips if no device is configured."""
    device = _require_env("NIDAQLIB_TEST_TC_DEVICE")
    primary = os.environ.get("NIDAQLIB_TEST_TC_CHANNEL_PRIMARY", "").strip() or f"{device}/ai0"
    secondary = os.environ.get("NIDAQLIB_TEST_TC_CHANNEL_SECONDARY", "").strip() or None
    tc_type = (os.environ.get("NIDAQLIB_TEST_TC_TYPE") or "K").strip().upper()
    return TcHardwareConfig(
        device=device,
        channel_primary=primary,
        channel_secondary=secondary,
        tc_type=tc_type,
        rate_hz=_optional_float("NIDAQLIB_TEST_TC_RATE_HZ", 10.0),
        min_degc=_optional_float("NIDAQLIB_TEST_TC_MIN_DEGC", -50.0),
        max_degc=_optional_float("NIDAQLIB_TEST_TC_MAX_DEGC", 200.0),
    )


# ---------------------------------------------------------------------------
# Channel-spec factories
# ---------------------------------------------------------------------------


def _resolve_tc_type(name: str) -> Any:
    """Map a single-letter TC type string to ``nidaqmx.constants.ThermocoupleType``."""
    from nidaqmx.constants import ThermocoupleType

    table = {
        "J": ThermocoupleType.J,
        "K": ThermocoupleType.K,
        "T": ThermocoupleType.T,
        "E": ThermocoupleType.E,
        "N": ThermocoupleType.N,
        "B": ThermocoupleType.B,
        "R": ThermocoupleType.R,
        "S": ThermocoupleType.S,
    }
    if name not in table:
        pytest.skip(f"unsupported NIDAQLIB_TEST_TC_TYPE={name!r}")
    return table[name]


def make_tc_channel(
    cfg: TcHardwareConfig,
    *,
    physical_channel: str | None = None,
    name: str | None = None,
) -> ChannelSpec:
    """Build a :class:`ThermocoupleInput` from the env-driven config."""
    return ThermocoupleInput(
        physical_channel=physical_channel or cfg.channel_primary,
        name=name,
        unit="degC",
        thermocouple_type=_resolve_tc_type(cfg.tc_type),
        min_val=cfg.min_degc,
        max_val=cfg.max_degc,
    )


def make_voltage_channel(
    cfg: TcHardwareConfig,
    *,
    physical_channel: str | None = None,
    name: str | None = None,
) -> ChannelSpec:
    """Build an :class:`AnalogInputVoltage` for modules that accept voltage AI.

    Most TC-only modules (9211 / 9212 / 9214) reject voltage AI; tests that
    use this factory should be prepared for an :class:`NIDaqBackendError`
    at start time and either ``xfail`` or skip on that path.
    """
    return AnalogInputVoltage(
        physical_channel=physical_channel or cfg.channel_primary,
        name=name,
        unit="V",
        min_val=-0.1,
        max_val=0.1,
    )


# ---------------------------------------------------------------------------
# Spec factories
# ---------------------------------------------------------------------------


@pytest.fixture
def tc_spec_on_demand(tc_config: TcHardwareConfig) -> TaskSpec:
    """One-channel TC spec with no Timing — valid for ``poll()``."""
    return TaskSpec(
        name="tc_on_demand",
        channels=[make_tc_channel(tc_config, name="primary")],
    )


@pytest.fixture
def tc_spec_finite(tc_config: TcHardwareConfig) -> TaskSpec:
    """One-channel TC spec configured for FINITE acquisition."""
    from nidaqlib import AcquisitionMode

    return TaskSpec(
        name="tc_finite",
        channels=[make_tc_channel(tc_config, name="primary")],
        timing=Timing(
            rate_hz=tc_config.rate_hz,
            mode=AcquisitionMode.FINITE,
            samples_per_channel=int(max(2, tc_config.rate_hz)),
        ),
    )


@pytest.fixture
def tc_spec_continuous(tc_config: TcHardwareConfig) -> TaskSpec:
    """One-channel TC spec configured for CONTINUOUS acquisition."""
    return TaskSpec(
        name="tc_continuous",
        channels=[make_tc_channel(tc_config, name="primary")],
        timing=Timing(rate_hz=tc_config.rate_hz),  # mode defaults to CONTINUOUS
    )


@pytest.fixture
def tc_spec_continuous_two_channel(tc_config: TcHardwareConfig) -> TaskSpec:
    """Two-channel TC spec; skips when no secondary channel is configured."""
    if tc_config.channel_secondary is None:
        pytest.skip("NIDAQLIB_TEST_TC_CHANNEL_SECONDARY not set — two-channel test skipped")
    return TaskSpec(
        name="tc_continuous_pair",
        channels=[
            make_tc_channel(tc_config, name="primary"),
            make_tc_channel(
                tc_config,
                physical_channel=tc_config.channel_secondary,
                name="secondary",
            ),
        ],
        timing=Timing(rate_hz=tc_config.rate_hz),
    )


# ---------------------------------------------------------------------------
# Sanity-check helpers shared across blocks
# ---------------------------------------------------------------------------


def assert_plausible_temperature(value: float, cfg: TcHardwareConfig, *, where: str = "") -> None:
    """Assert ``value`` falls inside the configured TC range with a margin.

    Hard-grounds the test against gross errors (open thermocouple → ~+inf;
    miswired CJC → tens of thousands of degrees). Uses the configured
    range plus a 50% slack on each end to tolerate ambient/hand-held TCs.
    """
    span = cfg.max_degc - cfg.min_degc
    lo = cfg.min_degc - 0.5 * span
    hi = cfg.max_degc + 0.5 * span
    assert lo <= value <= hi, (
        f"temperature {value!r} outside plausible range [{lo}, {hi}] "
        f"at {where!r}; check thermocouple wiring / type"
    )


def assert_close_float(actual: float | None, expected: float, *, where: str = "") -> None:
    """Assert an optional DAQ float is present and close to ``expected``."""
    assert actual is not None, f"missing float value at {where!r}"
    assert math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-12), (
        f"{actual!r} is not close to {expected!r} at {where!r}"
    )


# ---------------------------------------------------------------------------
# tmp dir per test — pytest already provides ``tmp_path``; re-export for clarity
# ---------------------------------------------------------------------------


@pytest.fixture
def hw_tmp_dir(tmp_path: Path) -> Path:
    """Alias around the built-in ``tmp_path`` fixture for hardware tests.

    Exists so the integration tests read symmetrically with the unit
    fixtures and to leave room for hardware-specific cleanup later
    (e.g. a TDMS file lockfile audit).
    """
    return tmp_path
