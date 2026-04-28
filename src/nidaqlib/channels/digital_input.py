"""Digital-input channel specifications (design doc §17).

Digital inputs do not require ``confirm=True`` — they only observe state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nidaqlib.channels.base import ChannelSpec, register_channel_kind


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalInput(ChannelSpec):
    """Digital-input line or port.

    Maps to ``Task.di_channels.add_di_chan`` on the NI side. ``physical_channel``
    accepts NI's line / port grammar (``Dev1/port0/line0``,
    ``Dev1/port0:7``, ...).

    Attributes:
        line_grouping_per_line: When ``True``, the backend treats each line
            as its own channel (NI ``LineGrouping.CHAN_PER_LINE``). Defaults
            to ``True`` so multi-line specs round-trip cleanly into per-line
            reads. Set to ``False`` for one-channel-for-all-lines
            (``CHAN_FOR_ALL_LINES``).
    """

    kind: ClassVar[str] = "di"
    line_grouping_per_line: bool = True


__all__ = ["DigitalInput"]
