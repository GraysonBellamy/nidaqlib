"""Channel specifications — :class:`ChannelSpec` and concrete subclasses."""

from __future__ import annotations

from nidaqlib.channels.analog_input import (
    AnalogInputBase,
    AnalogInputVoltage,
    ThermocoupleInput,
)
from nidaqlib.channels.analog_output import AnalogOutputVoltage
from nidaqlib.channels.base import ChannelSpec, register_channel_kind
from nidaqlib.channels.counter_input import (
    CounterEdgeCountInput,
    CounterFrequencyInput,
    CounterPeriodInput,
)
from nidaqlib.channels.counter_output import (
    CounterPulseFrequency,
    CounterPulseTicks,
    CounterPulseTime,
)
from nidaqlib.channels.digital_input import DigitalInput
from nidaqlib.channels.digital_output import DigitalOutput

__all__ = [
    "AnalogInputBase",
    "AnalogInputVoltage",
    "AnalogOutputVoltage",
    "ChannelSpec",
    "CounterEdgeCountInput",
    "CounterFrequencyInput",
    "CounterPeriodInput",
    "CounterPulseFrequency",
    "CounterPulseTicks",
    "CounterPulseTime",
    "DigitalInput",
    "DigitalOutput",
    "ThermocoupleInput",
    "register_channel_kind",
]
