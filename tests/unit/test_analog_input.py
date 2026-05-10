"""Tests for :class:`AnalogInputBase` and :class:`AnalogInputVoltage`.

Covers the per-channel ADC-timing knob inherited by every AI spec, plus
``AnalogInputVoltage``'s :class:`TerminalConfiguration` JSON round-trip.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    ADCTimingMode,
    AnalogInputVoltage,
    AutoZeroType,
    NIDaqValidationError,
    TerminalConfiguration,
    ThermocoupleInput,
    ThermocoupleType,
)
from nidaqlib.channels.base import ChannelSpec

# -- Validation: adc_timing_mode / adc_custom_timing_mode pairing ------------


def test_custom_timing_requires_custom_mode() -> None:
    """``adc_custom_timing_mode`` only makes sense with ``ADCTimingMode.CUSTOM``."""
    with pytest.raises(NIDaqValidationError, match=r"ADCTimingMode\.CUSTOM"):
        AnalogInputVoltage(
            physical_channel="Dev1/ai0",
            adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
            adc_custom_timing_mode=42,
        )


def test_custom_mode_requires_custom_timing() -> None:
    """``ADCTimingMode.CUSTOM`` without an integer is a configuration error."""
    with pytest.raises(NIDaqValidationError, match="adc_custom_timing_mode"):
        AnalogInputVoltage(
            physical_channel="Dev1/ai0",
            adc_timing_mode=ADCTimingMode.CUSTOM,
        )


def test_custom_mode_with_int_accepted() -> None:
    """Paired ``CUSTOM`` + integer is the only valid use of the custom field."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.CUSTOM,
        adc_custom_timing_mode=4242,
    )
    assert ch.adc_timing_mode is ADCTimingMode.CUSTOM
    assert ch.adc_custom_timing_mode == 4242


def test_validation_runs_on_thermocouple_too() -> None:
    """Inheriting through :class:`AnalogInputBase` means TC specs validate as well."""
    with pytest.raises(NIDaqValidationError, match=r"ADCTimingMode\.CUSTOM"):
        ThermocoupleInput(
            physical_channel="Dev1/ai0",
            thermocouple_type=ThermocoupleType.K,
            min_val=0.0,
            max_val=100.0,
            adc_timing_mode=ADCTimingMode.HIGH_SPEED,
            adc_custom_timing_mode=1,
        )


# -- Round-trip: AnalogInputVoltage -----------------------------------------


@pytest.mark.parametrize(
    "mode",
    [
        ADCTimingMode.AUTOMATIC,
        ADCTimingMode.HIGH_RESOLUTION,
        ADCTimingMode.HIGH_SPEED,
        ADCTimingMode.BEST_50_HZ_REJECTION,
        ADCTimingMode.BEST_60_HZ_REJECTION,
    ],
)
def test_ai_voltage_adc_timing_mode_round_trip(mode: ADCTimingMode) -> None:  # type: ignore[no-any-unimported]
    """Every non-``CUSTOM`` mode survives ``to_dict``/``from_dict``."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=mode,
    )
    payload = ch.to_dict()
    assert payload["adc_timing_mode"] == mode.value
    assert isinstance(payload["adc_timing_mode"], int)
    restored = AnalogInputVoltage.from_dict(payload)
    assert restored == ch
    assert restored.adc_timing_mode is mode


def test_ai_voltage_custom_timing_round_trip() -> None:
    """``CUSTOM`` mode plus its integer code round-trip together."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.CUSTOM,
        adc_custom_timing_mode=7,
    )
    payload = ch.to_dict()
    restored = AnalogInputVoltage.from_dict(payload)
    assert restored == ch
    assert restored.adc_custom_timing_mode == 7


def test_ai_voltage_terminal_config_round_trip() -> None:
    """``TerminalConfiguration`` is serialised as its ``.value`` int."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        terminal_config=TerminalConfiguration.RSE,
    )
    payload = ch.to_dict()
    assert isinstance(payload["terminal_config"], int)
    assert payload["terminal_config"] == TerminalConfiguration.RSE.value
    restored = AnalogInputVoltage.from_dict(payload)
    assert restored == ch
    assert restored.terminal_config is TerminalConfiguration.RSE


def test_ai_voltage_no_adc_timing_mode_is_default() -> None:
    """Unspecified ADC timing leaves both fields ``None``."""
    ch = AnalogInputVoltage(physical_channel="Dev1/ai0")
    assert ch.adc_timing_mode is None
    assert ch.adc_custom_timing_mode is None
    payload = ch.to_dict()
    assert payload["adc_timing_mode"] is None


def test_ai_voltage_unknown_adc_timing_rejected() -> None:
    """A bogus enum value on deserialisation raises :class:`NIDaqValidationError`."""
    payload = AnalogInputVoltage(physical_channel="Dev1/ai0").to_dict()
    payload["adc_timing_mode"] = -1
    with pytest.raises(NIDaqValidationError, match="ADCTimingMode"):
        AnalogInputVoltage.from_dict(payload)


def test_ai_voltage_unknown_terminal_config_rejected() -> None:
    """A bogus terminal-config int on deserialisation surfaces as a typed error."""
    payload = AnalogInputVoltage(physical_channel="Dev1/ai0").to_dict()
    # NI uses ``TerminalConfiguration.DEFAULT == -1``, so -1 is valid;
    # pick a value far outside the enum range.
    payload["terminal_config"] = -999_999
    with pytest.raises(NIDaqValidationError, match="TerminalConfiguration"):
        AnalogInputVoltage.from_dict(payload)


def test_registry_dispatches_to_ai_voltage_with_timing() -> None:
    """``ChannelSpec.from_dict`` registry path preserves the ADC-timing field."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.BEST_60_HZ_REJECTION,
    )
    restored = ChannelSpec.from_dict(ch.to_dict())
    assert isinstance(restored, AnalogInputVoltage)
    assert restored == ch


# -- Auto-zero mode (inherited from AnalogInputBase) -------------------------


def test_auto_zero_mode_default_is_none() -> None:
    """Unspecified auto-zero leaves NI's per-device default in place."""
    ch = AnalogInputVoltage(physical_channel="Dev1/ai0")
    assert ch.auto_zero_mode is None
    payload = ch.to_dict()
    assert payload["auto_zero_mode"] is None


@pytest.mark.parametrize(
    "mode",
    [AutoZeroType.NONE, AutoZeroType.ONCE, AutoZeroType.EVERY_SAMPLE],
)
def test_ai_voltage_auto_zero_mode_round_trip(mode: AutoZeroType) -> None:  # type: ignore[no-any-unimported]
    """Every :class:`AutoZeroType` member survives ``to_dict``/``from_dict``."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        auto_zero_mode=mode,
    )
    payload = ch.to_dict()
    assert payload["auto_zero_mode"] == mode.value
    assert isinstance(payload["auto_zero_mode"], int)
    restored = AnalogInputVoltage.from_dict(payload)
    assert restored == ch
    assert restored.auto_zero_mode is mode


def test_thermocouple_auto_zero_mode_round_trip() -> None:
    """The TC spec inherits the same field and round-trip through the registry."""
    tc = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
        auto_zero_mode=AutoZeroType.ONCE,
    )
    restored = ChannelSpec.from_dict(tc.to_dict())
    assert isinstance(restored, ThermocoupleInput)
    assert restored == tc
    assert restored.auto_zero_mode is AutoZeroType.ONCE


def test_unknown_auto_zero_mode_rejected() -> None:
    """A bogus enum value on deserialisation raises :class:`NIDaqValidationError`."""
    payload = AnalogInputVoltage(physical_channel="Dev1/ai0").to_dict()
    payload["auto_zero_mode"] = -999_999
    with pytest.raises(NIDaqValidationError, match="AutoZeroType"):
        AnalogInputVoltage.from_dict(payload)


def test_auto_zero_and_adc_timing_compose() -> None:
    """The two AI-base knobs are independent — both survive a round-trip together."""
    ch = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
        auto_zero_mode=AutoZeroType.ONCE,
    )
    restored = AnalogInputVoltage.from_dict(ch.to_dict())
    assert restored == ch
    assert restored.adc_timing_mode is ADCTimingMode.HIGH_RESOLUTION
    assert restored.auto_zero_mode is AutoZeroType.ONCE
