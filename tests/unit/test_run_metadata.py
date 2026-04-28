"""Tests for :class:`RunMetadata` and the TDMS sidecar writer (§18.2 / §18.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from nidaqlib import (
    AnalogInputVoltage,
    DigitalEdgeStartTrigger,
    Edge,
    NIDaqValidationError,
    RunMetadata,
    TaskSpec,
    Timing,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)


def _spec(name: str) -> TaskSpec:
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel=f"Dev1/ai{name[-1]}")],
        timing=Timing(rate_hz=1000.0),
        trigger=DigitalEdgeStartTrigger(source="/Dev1/PFI0", edge=Edge.RISING),
    )


def test_run_metadata_round_trips_with_nested_specs() -> None:
    meta = RunMetadata.for_run(
        "run-001",
        task_specs={"task_a": _spec("task_a"), "task_b": _spec("task_b")},
        user_metadata={"operator": "alice", "recipe": "exp42"},
        started_at=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
    )
    payload = meta.to_dict()
    # JSON-serialisable end-to-end.
    serialised = json.dumps(payload)
    restored = RunMetadata.from_dict(json.loads(serialised))
    assert restored.run_id == "run-001"
    assert restored.started_at == datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    assert set(restored.task_specs) == {"task_a", "task_b"}
    assert isinstance(restored.task_specs["task_a"].trigger, DigitalEdgeStartTrigger)
    assert restored.user_metadata["operator"] == "alice"


def test_run_metadata_for_run_populates_versions() -> None:
    meta = RunMetadata.for_run("r")
    # Version field is populated even without nidaqmx installed.
    assert isinstance(meta.nidaqlib_version, str)
    assert meta.nidaqlib_version != ""
    assert isinstance(meta.python_version, str)
    assert isinstance(meta.platform, str)


def test_run_metadata_rejects_missing_required_fields() -> None:
    with pytest.raises(NIDaqValidationError):
        RunMetadata.from_dict({"started_at": "2026-01-01T00:00:00+00:00"})
    with pytest.raises(NIDaqValidationError):
        RunMetadata.from_dict({"run_id": "r"})


def test_run_metadata_rejects_bad_started_at() -> None:
    with pytest.raises(NIDaqValidationError):
        RunMetadata.from_dict({"run_id": "r", "started_at": "not-a-date"})


def test_run_metadata_rejects_non_mapping_task_specs() -> None:
    with pytest.raises(NIDaqValidationError):
        RunMetadata.from_dict(
            {"run_id": "r", "started_at": "2026-01-01T00:00:00+00:00", "task_specs": []}
        )


def test_sidecar_path_for_replaces_tdms_extension(tmp_path: Path) -> None:
    assert sidecar_path_for(tmp_path / "run.tdms") == tmp_path / "run.metadata.json"
    # Case-insensitive on the suffix:
    assert sidecar_path_for(tmp_path / "RUN.TDMS") == tmp_path / "RUN.metadata.json"
    # Non-tdms suffix → append.
    assert sidecar_path_for(tmp_path / "run.parquet") == tmp_path / "run.parquet.metadata.json"


def test_write_sidecar_round_trip(tmp_path: Path) -> None:
    tdms = tmp_path / "subdir" / "run.tdms"
    meta = RunMetadata.for_run(
        "run-001",
        task_specs={"t": _spec("t")},
        user_metadata={"sample": "X"},
        started_at=datetime(2026, 4, 28, tzinfo=UTC),
    )
    sidecar = write_sidecar(tdms, meta)
    assert sidecar == tmp_path / "subdir" / "run.metadata.json"
    assert sidecar.exists()

    restored = read_sidecar(tdms)
    assert restored.run_id == "run-001"
    assert restored.user_metadata == {"sample": "X"}


def test_read_sidecar_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_sidecar(tmp_path / "nonexistent.tdms")


def test_read_sidecar_rejects_non_object(tmp_path: Path) -> None:
    sidecar = tmp_path / "run.metadata.json"
    sidecar.write_text("[]", encoding="utf-8")
    with pytest.raises(NIDaqValidationError):
        read_sidecar(tmp_path / "run.tdms")
