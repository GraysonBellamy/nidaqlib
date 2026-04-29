"""Block D — sync facade + CLI smoke against real TC hardware.

The sync facade is exercised end-to-end via :class:`Daq.open_task` running
in a worker thread (so we do not collide with the integration suite's
top-level event loop).

The :mod:`nidaqlib.cli` tools default to :class:`AnalogInputVoltage`
specs but accept ``--thermocouple-type`` for TC-only modules. ``test_d3``
verifies that without the flag, ``nidaq-read`` against a TC module fails
with a typed NI error (regression tripwire); ``test_d4`` verifies that
``--thermocouple-type K`` against the same module succeeds end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from anyio.to_thread import run_sync

from nidaqlib.sync import Daq

from .conftest import assert_plausible_temperature

if TYPE_CHECKING:
    from nidaqlib import TaskSpec

    from .conftest import TcHardwareConfig


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# D1 — Daq.open_task → poll() in a sync context, dispatched from anyio
# ---------------------------------------------------------------------------


async def test_d1_sync_facade_poll(
    tc_config: TcHardwareConfig,
    tc_spec_on_demand: TaskSpec,
) -> None:
    """``Daq.open_task`` in a worker thread returns a sane :class:`DaqReading`.

    Driving the sync facade from a sync function nested inside ``run_sync``
    is the canonical mode (a script or notebook); we exercise that here
    rather than importing :mod:`anyio` inside the sync code path.
    """

    def _sync_capture() -> dict[str, float]:
        with Daq.open_task(tc_spec_on_demand) as session:
            assert session.is_started is True
            reading = session.poll()
        # Coerce to a plain dict so the worker-thread payload contains no
        # async-bound state.
        return {ch: float(v) for ch, v in reading.values.items()}

    values: dict[str, float] = await run_sync(_sync_capture)

    assert "primary" in values
    assert_plausible_temperature(values["primary"], tc_config, where="D1.poll")


# ---------------------------------------------------------------------------
# D2 — nidaq-info reports device + driver in JSON
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke a console-script entry point as a subprocess.

    Uses ``python -m nidaqlib.cli.<tool>`` so the test does not depend on
    the installed-script wrappers (which only exist after ``uv sync``).
    """
    return subprocess.run(
        [sys.executable, "-m", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )


def test_d2_nidaq_info_json_lists_device(tc_config: TcHardwareConfig) -> None:
    """``nidaq-info --json`` includes the configured device.

    Sync test (no ``anyio`` decoration) because the CLI is invoked as a
    subprocess.
    """
    proc = _run_cli("nidaqlib.cli.info", "--json")
    assert proc.returncode == 0, f"nidaq-info failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["nidaqlib_version"]
    assert payload["nidaqmx_version"], "nidaqmx-python should be installed"
    device_names = {d["name"] for d in payload["devices"]}
    assert tc_config.device in device_names, (
        f"configured device {tc_config.device!r} not in nidaq-info output: {device_names!r}"
    )


def test_d2_nidaq_list_human_lists_device(tc_config: TcHardwareConfig) -> None:
    """``nidaq-list`` (human-readable) prints a line for the device."""
    proc = _run_cli("nidaqlib.cli.list")
    assert proc.returncode == 0, f"nidaq-list failed: {proc.stderr}"
    assert tc_config.device in proc.stdout, (
        f"device {tc_config.device!r} not present in nidaq-list output:\n{proc.stdout}"
    )


def test_d2_nidaq_list_device_json_lists_ai_channels(
    tc_config: TcHardwareConfig,
) -> None:
    """``nidaq-list <device> --json`` reports AI physical channels."""
    proc = _run_cli("nidaqlib.cli.list", tc_config.device, "--json")
    assert proc.returncode == 0, f"nidaq-list <device> failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["device"] == tc_config.device
    ai_channels = payload["ai_channels"]
    assert ai_channels, "device reports no AI channels"
    # Configured primary channel should be in the listing.
    assert tc_config.channel_primary in ai_channels


# ---------------------------------------------------------------------------
# D3 — without --thermocouple-type, nidaq-read on a TC module fails clearly
# ---------------------------------------------------------------------------


def test_d3_nidaq_read_voltage_mode_rejected_on_tc_module(
    tc_config: TcHardwareConfig,
) -> None:
    """``nidaq-read`` defaults to voltage AI; a TC-only module rejects it.

    Tripwire: if a future operator's module DOES accept voltage AI (e.g.
    USB-6001), this test will start failing-as-passing — at which point the
    failure mode has changed and the assertion below should be revisited.
    Today (NI 9214 + 26.3 driver) the CLI exits non-zero with the NI rejection
    surfaced on stderr.
    """
    proc = _run_cli("nidaqlib.cli.read", tc_config.channel_primary)
    assert proc.returncode != 0, (
        "nidaq-read against a TC-only module unexpectedly succeeded; "
        "either the module accepts voltage AI or the CLI now infers TC mode."
    )
    assert "nidaq-read:" in proc.stderr


# ---------------------------------------------------------------------------
# D4 — with --thermocouple-type K, nidaq-read drives the same TC module
# ---------------------------------------------------------------------------


def test_d4_nidaq_read_thermocouple_mode(
    tc_config: TcHardwareConfig,
) -> None:
    """``nidaq-read --thermocouple-type K`` returns a sane temperature.

    Validates the CLI's TC mode end-to-end on real hardware. Default range
    (-50 to 200 degC) covers room-temperature measurement.
    """
    proc = _run_cli(
        "nidaqlib.cli.read",
        tc_config.channel_primary,
        "--thermocouple-type",
        tc_config.tc_type,
        "--json",
    )
    assert proc.returncode == 0, f"nidaq-read --thermocouple-type failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    values: dict[str, float] = payload["values"]
    assert values, "no channel values returned"
    (temperature,) = values.values()
    assert_plausible_temperature(temperature, tc_config, where="D4.tc_read")
    units: dict[str, str] = payload["units"]
    assert all(u == "degC" for u in units.values()), units
