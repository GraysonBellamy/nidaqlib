"""Tests for the :class:`ThermocoupleInput` channel spec.

Covers:

- kw-only / frozen invariants identical to the AI voltage spec.
- ``to_dict`` / ``from_dict`` round-trip via ``ChannelSpec``'s registry.
- Backend dispatch (fake backend records the spec verbatim).
- Inherited ADC-timing-mode plumbing from :class:`AnalogInputBase`.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nidaqlib import (
    ADCTimingMode,
    CJCSource,
    NIDaqValidationError,
    TaskSpec,
    TemperatureUnits,
    ThermocoupleInput,
    ThermocoupleType,
)
from nidaqlib.channels.base import ChannelSpec


def test_kw_only_construction() -> None:
    """Positional args are rejected (kw_only=True invariant)."""
    with pytest.raises(TypeError):
        # Positional construction must fail at runtime; the multiple type-check
        # errors are intentional and are silenced for the whole call.
        ThermocoupleInput("Dev1/ai0", ThermocoupleType.K, 0.0, 100.0)  # type: ignore[call-arg, arg-type, misc]  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_frozen() -> None:
    """Assignment after construction raises :class:`FrozenInstanceError`."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
    )
    with pytest.raises(FrozenInstanceError):
        tc.min_val = 1.0  # type: ignore[misc]


def test_default_units_is_deg_c() -> None:
    """Default :class:`TemperatureUnits` is ``DEG_C``."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
    )
    assert tc.units == TemperatureUnits.DEG_C


def test_round_trip_via_subclass() -> None:
    """``ThermocoupleInput.to_dict`` → ``from_dict`` is the identity."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        name="oven",
        unit="degC",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
        cjc_source=CJCSource.BUILT_IN,
        cjc_val=25.0,
        units=TemperatureUnits.DEG_C,
    )
    payload = tc.to_dict()
    assert payload["kind"] == "thermocouple"
    # Enums round-trip via their .value (an int).
    assert isinstance(payload["thermocouple_type"], int)
    assert isinstance(payload["units"], int)
    restored = ThermocoupleInput.from_dict(payload)
    assert restored == tc


def test_round_trip_via_registry_dispatch() -> None:
    """``ChannelSpec.from_dict`` dispatches by ``kind`` to the right subclass."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai1",
        thermocouple_type=ThermocoupleType.J,
        min_val=-100.0,
        max_val=200.0,
    )
    restored = ChannelSpec.from_dict(tc.to_dict())
    assert isinstance(restored, ThermocoupleInput)
    assert restored == tc


def test_round_trip_no_cjc_source() -> None:
    """Optional ``cjc_source`` survives a None-round-trip."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.T,
        min_val=0.0,
        max_val=100.0,
    )
    assert tc.cjc_source is None
    assert ThermocoupleInput.from_dict(tc.to_dict()) == tc


def test_unknown_thermocouple_type_rejected() -> None:
    """Bogus enum values surface as :class:`NIDaqValidationError`."""
    payload: dict[str, object] = {
        "kind": "thermocouple",
        "physical_channel": "Dev1/ai0",
        "name": None,
        "unit": None,
        "metadata": {},
        "thermocouple_type": -999,
        "min_val": 0.0,
        "max_val": 100.0,
        "cjc_source": None,
        "cjc_val": None,
        "units": TemperatureUnits.DEG_C.value,
        "adc_timing_mode": None,
        "adc_custom_timing_mode": None,
    }
    with pytest.raises(NIDaqValidationError):
        ThermocoupleInput.from_dict(payload)


def test_taskspec_with_thermocouple_round_trips() -> None:
    """A :class:`TaskSpec` carrying a TC channel survives a JSON round-trip."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
    )
    spec = TaskSpec(name="oven_task", channels=[tc])
    restored = TaskSpec.from_dict(spec.to_dict())
    assert restored == spec


# -- ADC timing mode (inherited from AnalogInputBase) ------------------------


def test_thermocouple_adc_timing_default_is_none() -> None:
    """Unspecified ADC timing mode leaves NI's per-device default in place."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
    )
    assert tc.adc_timing_mode is None
    assert tc.adc_custom_timing_mode is None


def test_thermocouple_with_adc_timing_round_trips() -> None:
    """``adc_timing_mode`` survives a JSON round-trip via ``.value``."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
        adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
    )
    payload = tc.to_dict()
    assert payload["adc_timing_mode"] == ADCTimingMode.HIGH_RESOLUTION.value
    assert isinstance(payload["adc_timing_mode"], int)
    restored = ThermocoupleInput.from_dict(payload)
    assert restored == tc
    assert restored.adc_timing_mode is ADCTimingMode.HIGH_RESOLUTION
