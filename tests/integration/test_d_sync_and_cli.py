"""Block D — sync facade + CLI smoke against real TC hardware.

The sync facade is exercised end-to-end via :class:`Daq.open_task` running
in a worker thread (so we do not collide with the integration suite's
top-level event loop).

The :mod:`nidaqlib.cli` tools currently build :class:`AnalogInputVoltage`
specs internally — they cannot drive a TC-only module. ``test_d3`` runs
``nidaq-info`` (which works on any device class) and asserts the device
shows up; the ``nidaq-read`` / ``nidaq-capture`` gap is captured as an
``xfail`` so the limitation is durable rather than silent.
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
# D3 — known limitation: nidaq-read / nidaq-capture build AI-voltage specs
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "nidaq-read builds an AnalogInputVoltage spec internally; TC-only "
        "modules (NI-9211/9212/9214) reject the channel type. Track this by "
        "adding a --thermocouple-type option or a "
        "separate nidaq-tc command."
    ),
    strict=False,
)
def test_d3_nidaq_read_one_shot_on_tc_module(
    tc_config: TcHardwareConfig,
) -> None:
    """``nidaq-read`` on a TC-only module is expected to fail today.

    If this test ever starts passing, the failure mode has changed (e.g.
    the CLI grew TC support, or the operator's module accepts voltage
    AI). Either way, that's news worth surfacing.
    """
    proc = _run_cli("nidaqlib.cli.read", tc_config.channel_primary)
    assert proc.returncode == 0, f"nidaq-read failed: {proc.stderr}"
    assert tc_config.channel_primary in proc.stdout
