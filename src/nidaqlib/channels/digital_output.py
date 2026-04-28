"""Digital-output channel specifications (design doc §17).

Digital outputs default to ``requires_confirm=True`` — they can drive
relays, valves, igniters, or other actuators.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nidaqlib.channels.base import ChannelSpec, register_channel_kind


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalOutput(ChannelSpec):
    """Digital-output line or port.

    Maps to ``Task.do_channels.add_do_chan`` on the NI side. Writes are gated
    through :meth:`DaqSession.write`, which requires ``confirm=True``
    whenever any target channel sets ``requires_confirm`` (design doc §17.1).

    Attributes:
        requires_confirm: When ``True``, every :meth:`DaqSession.write`
            targeting this channel must pass ``confirm=True``. Defaults to
            ``True`` — digital outputs are assumed to drive a real-world
            actuator unless the spec explicitly opts out.
        line_grouping_per_line: When ``True``, the backend treats each line
            as its own channel. Same semantics as
            :class:`DigitalInput.line_grouping_per_line`.
    """

    kind: ClassVar[str] = "do"
    requires_confirm: bool = True
    line_grouping_per_line: bool = True


__all__ = ["DigitalOutput"]
