"""Block F — TDMS sidecar metadata round-trip on real hardware.

This is the sidecar-metadata surface a TC-only module can drive; counters and
hardware triggers are not reachable. We capture a short TDMS run, write the
:class:`RunMetadata` sidecar alongside it, and verify the sidecar is
discoverable and round-trips through :func:`read_sidecar`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from nidaqlib import (
    RunMetadata,
    TaskSpec,
    TdmsLogging,
    open_device,
    read_sidecar,
    record,
    sidecar_path_for,
    write_sidecar,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import TcHardwareConfig


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# F1 — write a sidecar and confirm it round-trips
# ---------------------------------------------------------------------------


async def test_f1_sidecar_round_trip(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """Capture a short TDMS run, write the sidecar, and read it back."""
    pytest.importorskip("nptdms", reason="nptdms not installed")
    from nidaqmx.constants import LoggingMode

    tdms_path = hw_tmp_dir / "f1.tdms"
    spec = tc_spec_continuous.replace(logging=TdmsLogging(path=tdms_path, mode=LoggingMode.LOG))
    metadata = RunMetadata.for_run(
        run_id="hardware-day-tc-f1",
        task_specs={"tc_continuous": spec},
        user_metadata={
            "operator": "integration-suite",
            "thermocouple_type": tc_config.tc_type,
            "device": tc_config.device,
        },
    )

    async with (
        await open_device(spec) as session,
        record(session, chunk_size=max(2, int(tc_config.rate_hz // 2))) as (
            _rx,
            _summary,
        ),
    ):
        del session  # used only to start the task; samples flow into TDMS
        await anyio.sleep(1.5)

    # Write the sidecar after the run; the API allows it before, during,
    # or after acquisition.
    sidecar_path = write_sidecar(tdms_path, metadata)
    assert sidecar_path.exists()
    assert sidecar_path == sidecar_path_for(tdms_path)
    assert sidecar_path.suffix == ".json"
    assert sidecar_path.name.endswith(".metadata.json")

    restored = read_sidecar(tdms_path)
    assert restored.run_id == metadata.run_id
    assert restored.user_metadata == dict(metadata.user_metadata)
    # Task-spec round-trip preserves the TC channel kind and TC type.
    assert "tc_continuous" in restored.task_specs
    restored_spec = restored.task_specs["tc_continuous"]
    assert restored_spec.name == spec.name
    assert len(restored_spec.channels) == len(spec.channels)
    assert restored_spec.channels[0].kind == "thermocouple"


# ---------------------------------------------------------------------------
# F2 — sidecar_path_for naming convention
# ---------------------------------------------------------------------------


def test_f2_sidecar_path_naming(hw_tmp_dir: Path) -> None:
    """``sidecar_path_for`` derives ``<base>.metadata.json`` from a TDMS path."""
    tdms_path = hw_tmp_dir / "run.tdms"
    sidecar = sidecar_path_for(tdms_path)
    assert sidecar == hw_tmp_dir / "run.metadata.json"

    # Non-TDMS extensions get the suffix appended (defensive — the hardware
    # tests always pass `.tdms`, but this is the contract).
    other = sidecar_path_for(hw_tmp_dir / "data.bin")
    assert other == hw_tmp_dir / "data.bin.metadata.json"
