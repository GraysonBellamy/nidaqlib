"""Round-trip tests for the output / digital channel specs.

Asserts ``to_dict`` / ``from_dict`` parity for the three new kinds, the
default ``requires_confirm`` policy, and the fall-back behaviour of
``effective_safe_min`` / ``effective_safe_max`` on
:class:`AnalogOutputVoltage`.
"""

from __future__ import annotations

from nidaqlib import (
    AnalogOutputVoltage,
    ChannelSpec,
    DigitalInput,
    DigitalOutput,
)


def test_analog_output_voltage_defaults() -> None:
    spec = AnalogOutputVoltage(physical_channel="Dev1/ao0", name="heater")
    assert spec.kind == "ao_voltage"
    assert spec.requires_confirm is True  # outputs default to safe
    assert spec.effective_safe_min == spec.min_val
    assert spec.effective_safe_max == spec.max_val


def test_analog_output_voltage_safe_clamp_overrides() -> None:
    spec = AnalogOutputVoltage(
        physical_channel="Dev1/ao0",
        name="heater",
        min_val=-10.0,
        max_val=10.0,
        safe_min=0.0,
        safe_max=5.0,
    )
    assert spec.effective_safe_min == 0.0
    assert spec.effective_safe_max == 5.0


def test_digital_input_defaults() -> None:
    spec = DigitalInput(physical_channel="Dev1/port0/line0")
    assert spec.kind == "di"
    assert spec.line_grouping_per_line is True
    assert not hasattr(spec, "requires_confirm")  # DI never gates writes


def test_digital_output_defaults() -> None:
    spec = DigitalOutput(physical_channel="Dev1/port0/line0")
    assert spec.kind == "do"
    assert spec.requires_confirm is True


def test_round_trip_via_base_dispatch() -> None:
    """All output and digital specs round-trip through ``ChannelSpec.from_dict``."""
    originals = [
        AnalogOutputVoltage(
            physical_channel="Dev1/ao0",
            name="heater",
            safe_min=0.0,
            safe_max=5.0,
        ),
        DigitalInput(physical_channel="Dev1/port0/line0", name="trigger_in"),
        DigitalOutput(physical_channel="Dev1/port0/line1", name="valve"),
    ]
    for original in originals:
        rebuilt = ChannelSpec.from_dict(original.to_dict())
        assert type(rebuilt) is type(original)
        assert rebuilt == original
