"""Trigger specifications — :class:`TriggerSpec` and concrete subclasses.

A :class:`TriggerSpec` describes when a task should begin sampling, beyond
the always-on default of "start as soon as ``start_task`` returns." The
spec types mirror the structure of :class:`~nidaqlib.channels.ChannelSpec`:
a frozen, kw-only dataclass base with a ``kind: ClassVar[str]``
discriminator and a registry-driven :meth:`from_dict` for round-trip
through JSON. See design doc §8.1.

Concrete variants:

- :class:`DigitalEdgeStartTrigger` — start trigger off a PFI / RTSI edge.
- :class:`AnalogEdgeStartTrigger` — start trigger off an analog threshold.
- :class:`DigitalEdgeReferenceTrigger` — reference (mid-acquisition) trigger
  with a pretrigger-sample window.

The backend is responsible for translating these into NI calls
(``triggers.start_trigger.cfg_dig_edge_start_trig`` / ...). NI requires the
sample-clock timing to be configured *before* the trigger; the wrapper
preserves that ordering in :meth:`DaqSession._configure_sync`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Self

from nidaqlib.errors import NIDaqValidationError
from nidaqlib.tasks.spec import Edge

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class TriggerSpec:
    """Base class for task-level trigger configurations.

    Subclasses declare a non-empty :attr:`kind` and are registered via
    :func:`register_trigger_kind` so :meth:`from_dict` on the base can
    dispatch by discriminator.

    Attributes:
        source: NI terminal supplying the trigger (e.g. ``"PFI0"``,
            ``"/Dev1/PFI0"``, ``"/Dev1/ai/StartTrigger"``). Subclasses use
            this verbatim.
    """

    source: str

    kind: ClassVar[str] = ""
    """Discriminator used by :meth:`from_dict`. Concrete subclasses override."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict, including ``kind``."""
        payload = dataclasses.asdict(self)
        payload["kind"] = self.kind
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise; on the base, dispatch by ``kind`` to a registered subclass.

        Raises:
            NIDaqValidationError: ``kind`` is missing, unknown, or does not
                match the concrete class.
        """
        kind = data.get("kind")
        if cls is TriggerSpec:
            if not isinstance(kind, str):
                raise NIDaqValidationError(
                    f"trigger spec dict missing 'kind' discriminator (got {kind!r})"
                )
            target = _TRIGGER_REGISTRY.get(kind)
            if target is None:
                raise NIDaqValidationError(f"unknown trigger kind {kind!r}")
            return target.from_dict(data)  # type: ignore[return-value]
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        payload = {k: v for k, v in data.items() if k != "kind"}
        return cls(**payload)


_TRIGGER_REGISTRY: dict[str, type[TriggerSpec]] = {}


def register_trigger_kind[T: TriggerSpec](cls: type[T]) -> type[T]:
    """Register a concrete :class:`TriggerSpec` subclass for dispatch.

    Idempotent — re-registering the same ``kind`` is a no-op. Raises on a
    conflicting registration.
    """
    if not cls.kind:
        raise NIDaqValidationError(f"{cls.__name__} must declare a non-empty 'kind' ClassVar")
    existing = _TRIGGER_REGISTRY.get(cls.kind)
    if existing is None:
        _TRIGGER_REGISTRY[cls.kind] = cls
    elif existing is not cls:
        raise NIDaqValidationError(
            f"trigger kind {cls.kind!r} already bound to {existing.__name__}"
        )
    return cls


@register_trigger_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalEdgeStartTrigger(TriggerSpec):
    """Start the task on a digital edge from ``source``.

    Maps to ``task.triggers.start_trigger.cfg_dig_edge_start_trig``. After
    :meth:`DaqSession.start` returns, the task is *armed* — the first
    sample is acquired only after NI sees the configured edge on
    ``source``.

    Attributes:
        edge: Active edge of the trigger. Rising by default.
    """

    kind: ClassVar[str] = "digital_edge_start"
    edge: Edge = Edge.RISING

    def to_dict(self) -> dict[str, Any]:
        """Serialise enums to ``.value`` so the payload is JSON-encodable."""
        return {
            "kind": self.kind,
            "source": self.source,
            "edge": self.edge.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring :class:`Edge` from its string value."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        try:
            edge = Edge(data.get("edge", Edge.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown Edge {data.get('edge')!r}") from exc
        return cls(source=str(data["source"]), edge=edge)


class AnalogTriggerSlope(StrEnum):
    """Active slope for an analog edge trigger.

    Mirrors ``nidaqmx.constants.Slope``. Kept library-side so
    :class:`AnalogEdgeStartTrigger` round-trips through JSON without
    pulling NI's enum machinery into the serialisation layer.
    """

    RISING = "rising"
    FALLING = "falling"


@register_trigger_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogEdgeStartTrigger(TriggerSpec):
    """Start the task when an analog channel crosses ``level``.

    Maps to ``task.triggers.start_trigger.cfg_anlg_edge_start_trig``.

    Attributes:
        level: Threshold level, in the source channel's engineering units.
        slope: Active slope (rising / falling). Rising by default.
    """

    kind: ClassVar[str] = "analog_edge_start"
    level: float
    slope: AnalogTriggerSlope = AnalogTriggerSlope.RISING

    def to_dict(self) -> dict[str, Any]:
        """Serialise; encode :class:`AnalogTriggerSlope` to its string value."""
        return {
            "kind": self.kind,
            "source": self.source,
            "level": self.level,
            "slope": self.slope.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring :class:`AnalogTriggerSlope` from its value."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        try:
            slope = AnalogTriggerSlope(data.get("slope", AnalogTriggerSlope.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown AnalogTriggerSlope {data.get('slope')!r}") from exc
        return cls(
            source=str(data["source"]),
            level=float(data["level"]),
            slope=slope,
        )


@register_trigger_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalEdgeReferenceTrigger(TriggerSpec):
    """Reference trigger — capture ``pretrigger_samples`` before, the rest after.

    Maps to ``task.triggers.reference_trigger.cfg_dig_edge_ref_trig``. Only
    valid for finite acquisitions; NI rejects continuous + reference
    trigger combinations at configure time, and the wrapper does not
    second-guess that.

    Attributes:
        pretrigger_samples: Number of samples per channel to retain from
            *before* the edge fires. Must be > 0.
        edge: Active edge of the trigger. Rising by default.
    """

    kind: ClassVar[str] = "digital_edge_reference"
    pretrigger_samples: int
    edge: Edge = Edge.RISING

    def __post_init__(self) -> None:
        """Reject zero / negative pretrigger windows up-front.

        Raises:
            NIDaqValidationError: ``pretrigger_samples`` is not positive.
        """
        if self.pretrigger_samples <= 0:
            raise NIDaqValidationError(
                f"pretrigger_samples must be > 0, got {self.pretrigger_samples}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise; encode :class:`Edge` to its string value."""
        return {
            "kind": self.kind,
            "source": self.source,
            "pretrigger_samples": self.pretrigger_samples,
            "edge": self.edge.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring :class:`Edge` from its string value."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        try:
            edge = Edge(data.get("edge", Edge.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown Edge {data.get('edge')!r}") from exc
        return cls(
            source=str(data["source"]),
            pretrigger_samples=int(data["pretrigger_samples"]),
            edge=edge,
        )


__all__ = [
    "AnalogEdgeStartTrigger",
    "AnalogTriggerSlope",
    "DigitalEdgeReferenceTrigger",
    "DigitalEdgeStartTrigger",
    "TriggerSpec",
    "register_trigger_kind",
]
