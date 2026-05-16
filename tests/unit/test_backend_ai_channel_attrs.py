"""Tests for :class:`NidaqmxBackend` per-AI-channel attribute dispatch.

The backend reads back the channel object returned by ``add_ai_*_chan``
and writes the post-add per-channel properties (``ai_adc_timing_mode``,
``ai_adc_custom_timing_mode``, ``ai_auto_zero_mode``) on it. These tests
exercise that plumbing against a stand-in NI task — no hardware required.
"""

from __future__ import annotations

from typing import Any

import pytest

from nidaqlib import (
    ADCTimingMode,
    AnalogInputVoltage,
    AutoZeroType,
    NIDaqBackendError,
    ThermocoupleInput,
    ThermocoupleType,
)
from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend

_UNSET: Any = object()
"""Sentinel used to detect "the backend never wrote this attribute" in tests."""


class _FakeChannel:
    """Stand-in for the per-channel NI object that ``add_ai_*_chan`` returns.

    Initial attribute values are :data:`_UNSET` so a test can distinguish
    "the backend wrote ``None`` here" (impossible — the spec stops it
    earlier) from "the backend never touched this attribute."
    """

    def __init__(self) -> None:
        self.ai_adc_timing_mode: Any = _UNSET
        self.ai_adc_custom_timing_mode: Any = _UNSET
        self.ai_auto_zero_mode: Any = _UNSET


class _AiCollection:
    """Minimal stand-in for ``task.ai_channels`` covering the two ``add_*`` calls."""

    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel
        self.last_voltage_kwargs: dict[str, Any] | None = None
        self.last_thrmcpl_kwargs: dict[str, Any] | None = None

    def add_ai_voltage_chan(self, **kwargs: Any) -> _FakeChannel:
        self.last_voltage_kwargs = kwargs
        return self._channel

    def add_ai_thrmcpl_chan(self, **kwargs: Any) -> _FakeChannel:
        self.last_thrmcpl_kwargs = kwargs
        return self._channel


class _FakeTask:
    """Stand-in NI task carrying just enough surface for ``add_channel``."""

    def __init__(self) -> None:
        self.channel = _FakeChannel()
        self.ai_channels = _AiCollection(self.channel)
        self.name = "ai_test"


def test_no_attrs_leaves_channel_untouched() -> None:
    """A spec without any AI-base attrs set makes no property writes."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = AnalogInputVoltage(physical_channel="Dev1/ai0")

    backend.add_channel(task, spec)

    assert task.channel.ai_adc_timing_mode is _UNSET
    assert task.channel.ai_adc_custom_timing_mode is _UNSET
    assert task.channel.ai_auto_zero_mode is _UNSET


def test_ai_voltage_sets_adc_timing_mode() -> None:
    """A non-``CUSTOM`` mode is written to ``ai_adc_timing_mode``."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
    )

    backend.add_channel(task, spec)

    assert task.channel.ai_adc_timing_mode is ADCTimingMode.HIGH_RESOLUTION
    assert task.channel.ai_adc_custom_timing_mode is _UNSET
    assert task.channel.ai_auto_zero_mode is _UNSET


def test_thermocouple_sets_adc_timing_mode() -> None:
    """The TC dispatch path applies the same property write."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
        adc_timing_mode=ADCTimingMode.BEST_60_HZ_REJECTION,
    )

    backend.add_channel(task, spec)

    assert task.channel.ai_adc_timing_mode is ADCTimingMode.BEST_60_HZ_REJECTION
    assert task.channel.ai_adc_custom_timing_mode is _UNSET


def test_custom_mode_sets_both_properties() -> None:
    """``ADCTimingMode.CUSTOM`` writes both the mode and the integer code."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.CUSTOM,
        adc_custom_timing_mode=4321,
    )

    backend.add_channel(task, spec)

    assert task.channel.ai_adc_timing_mode is ADCTimingMode.CUSTOM
    assert task.channel.ai_adc_custom_timing_mode == 4321


def test_ai_voltage_sets_auto_zero_mode() -> None:
    """``auto_zero_mode`` is written to ``ai_auto_zero_mode`` independently."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        auto_zero_mode=AutoZeroType.ONCE,
    )

    backend.add_channel(task, spec)

    assert task.channel.ai_auto_zero_mode is AutoZeroType.ONCE
    assert task.channel.ai_adc_timing_mode is _UNSET


def test_thermocouple_sets_auto_zero_mode() -> None:
    """The TC dispatch path also applies auto-zero mode."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = ThermocoupleInput(
        physical_channel="Dev1/ai0",
        thermocouple_type=ThermocoupleType.K,
        min_val=0.0,
        max_val=100.0,
        auto_zero_mode=AutoZeroType.EVERY_SAMPLE,
    )

    backend.add_channel(task, spec)

    assert task.channel.ai_auto_zero_mode is AutoZeroType.EVERY_SAMPLE


def test_attrs_compose() -> None:
    """ADC timing mode and auto-zero mode are written independently in one add."""
    backend = NidaqmxBackend()
    task = _FakeTask()
    spec = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
        auto_zero_mode=AutoZeroType.ONCE,
    )

    backend.add_channel(task, spec)

    assert task.channel.ai_adc_timing_mode is ADCTimingMode.HIGH_RESOLUTION
    assert task.channel.ai_auto_zero_mode is AutoZeroType.ONCE


def test_unsupported_attribute_wraps_as_backend_error() -> None:
    """An NI ``DaqError`` from the property-set surfaces as :class:`NIDaqBackendError`."""
    import nidaqmx.errors

    class _RejectingChannel:
        """Stand-in channel object whose ``ai_adc_timing_mode`` setter rejects."""

        def __setattr__(self, name: str, value: object) -> None:
            if name == "ai_adc_timing_mode":
                raise nidaqmx.errors.DaqError(  # pyright: ignore[reportCallIssue]
                    "attribute not supported on this device",
                    -200077,
                )
            super().__setattr__(name, value)

    rejecting = _RejectingChannel()

    class _RejectingAi:
        def __init__(self) -> None:
            self.last_voltage_kwargs: dict[str, Any] | None = None

        def add_ai_voltage_chan(self, **kwargs: Any) -> _RejectingChannel:
            self.last_voltage_kwargs = kwargs
            return rejecting

    task = _FakeTask()
    task.ai_channels = _RejectingAi()  # type: ignore[assignment]

    backend = NidaqmxBackend()
    spec = AnalogInputVoltage(
        physical_channel="Dev1/ai0",
        adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
    )

    with pytest.raises(NIDaqBackendError) as exc_info:
        backend.add_channel(task, spec)

    raised = exc_info.value
    assert raised.context.ni_error_code == -200077
    assert raised.context.command_name == "add_channel"
