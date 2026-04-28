"""Counter-input channel specifications.

NI counters address three common applications:

- :class:`CounterFrequencyInput` — measure the frequency of a digital
  pulse train.
- :class:`CounterPeriodInput` — measure the period of a digital pulse
  train.
- :class:`CounterEdgeCountInput` — accumulate edge counts (encoders,
  totalisers).

Counter inputs are read-only — they observe state, never drive it — so
they do not carry the safety-gate metadata the AO/DO/CO specs do. The
backend dispatches via ``task.ci_channels.add_*``; see design doc §17.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Self

from nidaqlib.channels.base import ChannelSpec, register_channel_kind
from nidaqlib.errors import NIDaqValidationError
from nidaqlib.tasks.spec import Edge

if TYPE_CHECKING:
    from collections.abc import Mapping


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class CounterFrequencyInput(ChannelSpec):
    """Frequency-measurement counter-input channel.

    Maps to ``Task.ci_channels.add_ci_freq_chan`` on the NI side. NI uses
    ``(min_val, max_val)`` to choose timebases that resolve frequencies in
    the expected range.

    Attributes:
        min_val: Lower bound of the expected frequency, in Hz.
        max_val: Upper bound of the expected frequency, in Hz.
        edge: Edge of the input signal that increments the counter. Rising
            by default.
    """

    kind: ClassVar[str] = "ci_frequency"
    min_val: float
    max_val: float
    edge: Edge = Edge.RISING

    def to_dict(self) -> dict[str, Any]:
        """Serialise; encode :class:`Edge` to its string value."""
        payload = ChannelSpec.to_dict(self)
        payload["edge"] = self.edge.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring :class:`Edge` from its string value."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        payload = {k: v for k, v in data.items() if k != "kind"}
        try:
            payload["edge"] = Edge(payload.get("edge", Edge.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown Edge {payload.get('edge')!r}") from exc
        return cls(**payload)


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class CounterPeriodInput(ChannelSpec):
    """Period-measurement counter-input channel.

    Maps to ``Task.ci_channels.add_ci_period_chan`` on the NI side.

    Attributes:
        min_val: Lower bound of the expected period, in seconds.
        max_val: Upper bound of the expected period, in seconds.
        edge: Starting edge of the period measurement. Rising by default.
    """

    kind: ClassVar[str] = "ci_period"
    min_val: float
    max_val: float
    edge: Edge = Edge.RISING

    def to_dict(self) -> dict[str, Any]:
        """Serialise; encode :class:`Edge` to its string value."""
        payload = ChannelSpec.to_dict(self)
        payload["edge"] = self.edge.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring :class:`Edge` from its string value."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        payload = {k: v for k, v in data.items() if k != "kind"}
        try:
            payload["edge"] = Edge(payload.get("edge", Edge.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown Edge {payload.get('edge')!r}") from exc
        return cls(**payload)


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class CounterEdgeCountInput(ChannelSpec):
    """Edge-count counter-input channel.

    Maps to ``Task.ci_channels.add_ci_count_edges_chan`` on the NI side.
    Useful for encoders, totalisers, or anything that needs raw edge
    accumulation.

    Attributes:
        edge: Edge that increments / decrements the counter. Rising by
            default.
        initial_count: Starting value of the counter. Defaults to 0.
        count_up: When ``True`` (default), every active edge increments
            the counter; when ``False``, decrements. Mirrors NI's
            ``CountDirection.COUNT_UP`` / ``COUNT_DOWN``.
    """

    kind: ClassVar[str] = "ci_edge_count"
    edge: Edge = Edge.RISING
    initial_count: int = 0
    count_up: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialise; encode :class:`Edge` to its string value."""
        payload = ChannelSpec.to_dict(self)
        payload["edge"] = self.edge.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring :class:`Edge` from its string value."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        payload = {k: v for k, v in data.items() if k != "kind"}
        try:
            payload["edge"] = Edge(payload.get("edge", Edge.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown Edge {payload.get('edge')!r}") from exc
        return cls(**payload)


__all__ = [
    "CounterEdgeCountInput",
    "CounterFrequencyInput",
    "CounterPeriodInput",
]
