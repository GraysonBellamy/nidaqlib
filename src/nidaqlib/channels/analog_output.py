"""Analog-output channel specifications.

:class:`AnalogOutputVoltage` carries safety metadata (``safe_min`` /
``safe_max`` / ``requires_confirm``) enforced by :meth:`DaqSession.write`,
not silently clamped — see design doc §17.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from nidaqlib.channels.base import ChannelSpec, register_channel_kind

if TYPE_CHECKING:
    from nidaqmx.constants import TerminalConfiguration


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogOutputVoltage(ChannelSpec):  # type: ignore[no-any-unimported]
    """Voltage analog-output channel.

    Maps to ``Task.ao_channels.add_ao_voltage_chan`` on the NI side. Writes
    are gated through :meth:`DaqSession.write`, which rejects out-of-range
    values against ``safe_min`` / ``safe_max`` and requires
    ``confirm=True`` whenever any target channel sets
    ``requires_confirm`` (design doc §17.1).

    Attributes:
        min_val: Lower bound of the device output range, in volts.
        max_val: Upper bound of the device output range, in volts. NI uses
            ``(min, max)`` to select the output gain.
        safe_min: Optional lower-end safety clamp for application writes.
            ``None`` means "use ``min_val`` as the clamp." Out-of-range
            writes raise :class:`NIDaqValidationError` — never silently
            clamped.
        safe_max: Optional upper-end safety clamp. ``None`` means "use
            ``max_val``."
        requires_confirm: When ``True``, every :meth:`DaqSession.write`
            targeting this channel must pass ``confirm=True``. Defaults to
            ``True`` — outputs default to safe.
        terminal_config: Terminal configuration (RSE / DIFF / ...). ``None``
            lets NI pick the device default.
        custom_scale_name: Optional name of a pre-configured custom scale
            registered in MAX. When set, ``min_val`` / ``max_val`` are
            engineering units, not volts.
    """

    kind: ClassVar[str] = "ao_voltage"
    min_val: float = -10.0
    max_val: float = 10.0
    safe_min: float | None = None
    safe_max: float | None = None
    requires_confirm: bool = True
    terminal_config: TerminalConfiguration | None = None  # type: ignore[no-any-unimported]
    custom_scale_name: str | None = None

    @property
    def effective_safe_min(self) -> float:
        """Resolved lower clamp — falls back to :attr:`min_val`."""
        return self.safe_min if self.safe_min is not None else self.min_val

    @property
    def effective_safe_max(self) -> float:
        """Resolved upper clamp — falls back to :attr:`max_val`."""
        return self.safe_max if self.safe_max is not None else self.max_val


__all__ = ["AnalogOutputVoltage"]
