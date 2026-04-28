"""Counter-output channel specifications.

Counter outputs generate digital pulse trains. They drive external
hardware (timing signals, gates, motor motion) so they inherit the
safety-gate metadata from :class:`AnalogOutputVoltage` /
:class:`DigitalOutput`: writes go through :meth:`DaqSession.write` which
honours ``requires_confirm`` and the optional safe-range clamps. See
design doc §17.

Three flavours mirroring NI's ``add_co_pulse_chan_*`` family:

- :class:`CounterPulseFrequency` — specify frequency in Hz + duty cycle.
- :class:`CounterPulseTime` — specify high / low time in seconds.
- :class:`CounterPulseTicks` — specify high / low time in counter ticks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nidaqlib.channels.base import ChannelSpec, register_channel_kind


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class CounterPulseFrequency(ChannelSpec):
    """Pulse-train counter output specified by frequency + duty cycle.

    Maps to ``Task.co_channels.add_co_pulse_chan_freq`` on the NI side.

    Attributes:
        frequency: Pulse-train frequency, in Hz.
        duty_cycle: Fractional duty cycle in ``(0.0, 1.0)``. 0.5 = square
            wave.
        initial_delay: Optional delay before the first pulse, in seconds.
            Defaults to 0.
        idle_high: When ``True``, the line idles high (active-low pulses);
            otherwise idles low (active-high pulses).
        requires_confirm: When ``True``, every :meth:`DaqSession.write`
            targeting this channel must pass ``confirm=True``. Defaults
            to ``True`` — counter outputs default to safe.
    """

    kind: ClassVar[str] = "co_pulse_frequency"
    frequency: float
    duty_cycle: float = 0.5
    initial_delay: float = 0.0
    idle_high: bool = False
    requires_confirm: bool = True


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class CounterPulseTime(ChannelSpec):
    """Pulse-train counter output specified by high / low durations in seconds.

    Maps to ``Task.co_channels.add_co_pulse_chan_time`` on the NI side.

    Attributes:
        high_time: High-state duration, in seconds.
        low_time: Low-state duration, in seconds.
        initial_delay: Optional delay before the first pulse, in seconds.
        idle_high: When ``True``, the line idles high (active-low pulses).
        requires_confirm: Defaults to ``True``.
    """

    kind: ClassVar[str] = "co_pulse_time"
    high_time: float
    low_time: float
    initial_delay: float = 0.0
    idle_high: bool = False
    requires_confirm: bool = True


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class CounterPulseTicks(ChannelSpec):
    """Pulse-train counter output specified by high / low tick counts.

    Maps to ``Task.co_channels.add_co_pulse_chan_ticks`` on the NI side.
    The tick reference is given by ``source_terminal``.

    Attributes:
        source_terminal: NI terminal supplying the tick clock (e.g.
            ``"/Dev1/20MHzTimebase"``).
        high_ticks: Number of source ticks in the high state.
        low_ticks: Number of source ticks in the low state.
        initial_delay: Optional initial-delay tick count.
        idle_high: When ``True``, the line idles high.
        requires_confirm: Defaults to ``True``.
    """

    kind: ClassVar[str] = "co_pulse_ticks"
    source_terminal: str
    high_ticks: int
    low_ticks: int
    initial_delay: int = 0
    idle_high: bool = False
    requires_confirm: bool = True


__all__ = [
    "CounterPulseFrequency",
    "CounterPulseTicks",
    "CounterPulseTime",
]
