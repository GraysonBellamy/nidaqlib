"""Unit tests for dataclasses (kw-only, frozen, round-trip).

Covers the design-doc invariants the package relies on:

- Specs are kw-only (positional construction is rejected by the dataclass).
- Specs are frozen (assignment after construction raises).
- ``to_dict`` / ``from_dict`` round-trip preserves the channel-subclass
  identity via the ``kind`` discriminator.
- ``DaqBlock.__post_init__`` rejects shape mismatches.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import numpy as np
import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    AnalogOutputVoltage,
    CounterPulseFrequency,
    DaqBlock,
    DaqReading,
    Edge,
    NIDaqValidationError,
    TaskSpec,
    Timing,
)
from nidaqlib.channels.base import ChannelSpec


def _make_spec() -> TaskSpec:
    return TaskSpec(
        name="ai_demo",
        channels=[
            AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0", unit="V"),
            AnalogInputVoltage(physical_channel="Dev1/ai1", name="ch1", unit="V"),
        ],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.CONTINUOUS),
    )


# -- kw_only enforcement ------------------------------------------------------


def test_taskspec_is_kw_only() -> None:
    with pytest.raises(TypeError):
        TaskSpec("ai", [])  # type: ignore[misc]


def test_timing_is_kw_only() -> None:
    with pytest.raises(TypeError):
        Timing(1000.0)  # type: ignore[misc]


def test_analog_input_voltage_is_kw_only() -> None:
    with pytest.raises(TypeError):
        AnalogInputVoltage("Dev1/ai0")  # type: ignore[misc]


# -- frozen-ness --------------------------------------------------------------


def test_taskspec_is_frozen() -> None:
    spec = _make_spec()
    with pytest.raises(FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]


def test_analog_input_voltage_is_frozen() -> None:
    ch = AnalogInputVoltage(physical_channel="Dev1/ai0")
    with pytest.raises(FrozenInstanceError):
        ch.physical_channel = "Dev2/ai0"  # type: ignore[misc]


# -- channel registry / discriminator round-trip ------------------------------


def test_analog_input_voltage_round_trip() -> None:
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        name="thermo",
        unit="V",
        min_val=-5.0,
        max_val=5.0,
    )
    payload = ch.to_dict()
    assert payload["kind"] == "ai_voltage"
    restored = ChannelSpec.from_dict(payload)
    assert isinstance(restored, AnalogInputVoltage)
    assert restored == ch


def test_channel_spec_dispatch_unknown_kind() -> None:
    with pytest.raises(NIDaqValidationError):
        ChannelSpec.from_dict({"kind": "not_a_real_kind", "physical_channel": "x"})


def test_channel_spec_missing_kind() -> None:
    with pytest.raises(NIDaqValidationError):
        ChannelSpec.from_dict({"physical_channel": "x"})


def test_concrete_class_rejects_wrong_kind() -> None:
    with pytest.raises(NIDaqValidationError):
        AnalogInputVoltage.from_dict({"kind": "wrong", "physical_channel": "x"})


# -- timing round-trip --------------------------------------------------------


def test_timing_round_trip() -> None:
    timing = Timing(
        rate_hz=2000.0,
        mode=AcquisitionMode.FINITE,
        samples_per_channel=4096,
        active_edge=Edge.FALLING,
    )
    restored = Timing.from_dict(timing.to_dict())
    assert restored == timing


def test_timing_unknown_mode_rejected() -> None:
    with pytest.raises(NIDaqValidationError):
        Timing.from_dict({"rate_hz": 100.0, "mode": "not_a_mode"})


def test_timing_rejects_nonpositive_rate() -> None:
    with pytest.raises(NIDaqValidationError):
        Timing(rate_hz=0.0)


def test_output_safe_range_must_be_ordered() -> None:
    with pytest.raises(NIDaqValidationError):
        AnalogOutputVoltage(physical_channel="Dev1/ao0", safe_min=5.0, safe_max=0.0)


def test_counter_output_rejects_bad_duty_cycle() -> None:
    with pytest.raises(NIDaqValidationError):
        CounterPulseFrequency(
            physical_channel="Dev1/ctr0",
            frequency=1000.0,
            duty_cycle=1.5,
        )


# -- task-spec round-trip -----------------------------------------------------


def test_task_spec_round_trip() -> None:
    spec = _make_spec()
    payload = spec.to_dict()
    restored = TaskSpec.from_dict(payload)
    assert restored.name == spec.name
    assert restored.timing == spec.timing
    assert len(restored.channels) == len(spec.channels)
    for got, want in zip(restored.channels, spec.channels, strict=True):
        assert isinstance(got, AnalogInputVoltage)
        assert got == want


def test_task_spec_rejects_empty_channels() -> None:
    with pytest.raises(NIDaqValidationError):
        TaskSpec(name="empty", channels=[])


def test_task_spec_copies_channels() -> None:
    channels = [AnalogInputVoltage(physical_channel="Dev1/ai0")]
    spec = TaskSpec(name="immutable", channels=channels)
    channels.append(AnalogInputVoltage(physical_channel="Dev1/ai1"))
    assert len(spec.channels) == 1


def test_task_spec_rejects_unknown_trigger_kind() -> None:
    spec = _make_spec()
    payload = spec.to_dict()
    payload["trigger"] = {"kind": "no_such_trigger", "source": "/Dev1/PFI0"}
    with pytest.raises(NIDaqValidationError):
        TaskSpec.from_dict(payload)


def test_task_spec_rejects_trigger_without_kind() -> None:
    spec = _make_spec()
    payload = spec.to_dict()
    payload["trigger"] = {"source": "/Dev1/PFI0"}
    with pytest.raises(NIDaqValidationError):
        TaskSpec.from_dict(payload)


# -- DaqBlock shape invariant -------------------------------------------------


def _make_block(*, n_channels: int, n_samples: int, data: np.ndarray) -> DaqBlock:
    now = datetime.now(UTC)
    return DaqBlock(
        device="dev",
        task="ai_demo",
        channels=tuple(f"ch{i}" for i in range(n_channels)),
        data=data,
        block_index=0,
        first_sample_index=0,
        samples_per_channel=n_samples,
        sample_rate_hz=1000.0,
        dt_s=0.001,
        task_started_at=now,
        t0=now,
        monotonic_ns=0,
        read_started_at=now,
        read_finished_at=now,
        elapsed_s=0.0,
        units={f"ch{i}": "V" for i in range(n_channels)},
    )


def test_daq_block_accepts_matching_shape() -> None:
    block = _make_block(
        n_channels=2,
        n_samples=4,
        data=np.zeros((2, 4), dtype=np.float64),
    )
    assert block.data.shape == (2, 4)


def test_daq_block_rejects_wrong_n_channels() -> None:
    with pytest.raises(NIDaqValidationError):
        _make_block(
            n_channels=2,
            n_samples=4,
            data=np.zeros((3, 4), dtype=np.float64),
        )


def test_daq_block_rejects_wrong_n_samples() -> None:
    with pytest.raises(NIDaqValidationError):
        _make_block(
            n_channels=2,
            n_samples=4,
            data=np.zeros((2, 5), dtype=np.float64),
        )


# -- DaqReading: minimal construction smoke ----------------------------------


def test_daq_reading_construction() -> None:
    now = datetime.now(UTC)
    reading = DaqReading(
        device="dev",
        task="ai_demo",
        values={"ch0": 1.5},
        units={"ch0": "V"},
        requested_at=now,
        received_at=now,
        midpoint_at=now,
        monotonic_ns=0,
        latency_s=0.001,
    )
    assert math.isclose(reading.values["ch0"], 1.5)
    assert reading.error is None
