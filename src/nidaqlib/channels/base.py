"""Channel specification base type and (de)serialization registry.

A :class:`ChannelSpec` is a frozen, kw-only dataclass describing one NI
physical channel and the application-side metadata the recorder / sink layer
needs to label it. Concrete subclasses declare a ``kind: ClassVar[str]``
discriminator (``"ai_voltage"``, ``"thermocouple"``, ...) so that
:meth:`from_dict` round-trips through JSON / TOML without losing the
subclass identity. See design doc §8.2 and §18.3.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Self, cast

from nidaqlib.errors import NIDaqValidationError


def _empty_metadata() -> dict[str, str | int | float | bool]:
    return {}


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelSpec:
    """Application-facing description of one DAQ channel.

    Attributes:
        physical_channel: NI physical channel identifier, e.g. ``"Dev1/ai0"``.
        name: Optional friendly name; defaults to the physical channel.
        unit: Optional engineering unit string (``"V"``, ``"degC"``, ...).
            Used by sinks for column headers; not interpreted by the backend.
        metadata: Free-form scalar metadata propagated into emitted records.
    """

    physical_channel: str
    name: str | None = None
    unit: str | None = None
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)

    kind: ClassVar[str] = ""
    """Discriminator used by :meth:`from_dict`. Concrete subclasses override."""

    def __post_init__(self) -> None:
        """Validate and freeze common channel metadata."""
        if not self.physical_channel:
            raise NIDaqValidationError("physical_channel must be a non-empty string")
        if self.name is not None and not self.name:
            raise NIDaqValidationError("name must be non-empty when provided")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def display_name(self) -> str:
        """Return ``name`` if set, otherwise the physical channel."""
        return self.name if self.name is not None else self.physical_channel

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON/TOML-friendly dict, including ``kind``.

        Returns:
            A dict carrying ``kind`` plus every dataclass field. Mappings are
            copied to plain ``dict`` so the result is JSON-encodable.
        """
        payload: dict[str, Any] = {}
        for spec in dataclasses.fields(self):
            value = getattr(self, spec.name)
            if isinstance(value, Mapping):
                value = dict(cast("Mapping[str, Any]", value))
            payload[spec.name] = value
        payload["kind"] = self.kind
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise from a dict produced by :meth:`to_dict`.

        On the base class, this dispatches to the registered subclass for the
        ``kind`` discriminator. On a concrete subclass, this validates that
        ``kind`` matches and constructs the dataclass directly.

        Args:
            data: Mapping carrying the ``kind`` discriminator and field values.

        Raises:
            NIDaqValidationError: ``kind`` is missing, unknown, or does not
                match the concrete class.
        """
        kind = data.get("kind")
        if cls is ChannelSpec:
            if not isinstance(kind, str):
                raise NIDaqValidationError(
                    f"channel spec dict missing 'kind' discriminator (got {kind!r})"
                )
            target = _CHANNEL_REGISTRY.get(kind)
            if target is None:
                raise NIDaqValidationError(f"unknown channel kind {kind!r}")
            # Mypy can't see that the registered class returns Self here; the
            # dispatch is dynamic by design, so cast at the boundary.
            return target.from_dict(data)  # type: ignore[return-value]
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        payload = {k: v for k, v in data.items() if k != "kind"}
        return cls(**payload)


_CHANNEL_REGISTRY: dict[str, type[ChannelSpec]] = {}


def register_channel_kind[C: ChannelSpec](cls: type[C]) -> type[C]:
    """Register a concrete :class:`ChannelSpec` subclass for dispatch.

    Used as a decorator on the subclass definition. Idempotent — re-registering
    the same ``kind`` is a no-op (helps reload-friendliness in notebooks).

    Args:
        cls: The subclass to register. Must declare a non-empty ``kind``.

    Returns:
        ``cls`` (so the decorator is transparent).

    Raises:
        NIDaqValidationError: ``cls.kind`` is empty or already bound to a
            different class.
    """
    if not cls.kind:
        raise NIDaqValidationError(f"{cls.__name__} must declare a non-empty 'kind' ClassVar")
    existing = _CHANNEL_REGISTRY.get(cls.kind)
    if existing is None:
        _CHANNEL_REGISTRY[cls.kind] = cls
    elif existing is not cls:
        raise NIDaqValidationError(
            f"channel kind {cls.kind!r} already bound to {existing.__name__}"
        )
    return cls


__all__ = ["ChannelSpec", "register_channel_kind"]
