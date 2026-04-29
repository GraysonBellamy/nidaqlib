"""Analog-input channel specifications.

Includes voltage and thermocouple input specs (design doc §8.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Self

from nidaqlib.channels.base import ChannelSpec, register_channel_kind
from nidaqlib.errors import NIDaqValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nidaqmx.constants import (
        CJCSource,
        TemperatureUnits,
        TerminalConfiguration,
        ThermocoupleType,
    )


def _default_temperature_units() -> Any:
    """Return :class:`nidaqmx.constants.TemperatureUnits.DEG_C`.

    Lazy import keeps ``nidaqlib.channels.analog_input`` importable in
    contexts where ``nidaqmx`` resolves only at call time (e.g. lazy
    install hooks). The factory is invoked once per dataclass construction.
    """
    from nidaqmx.constants import TemperatureUnits  # noqa: PLC0415

    return TemperatureUnits.DEG_C


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputVoltage(ChannelSpec):  # type: ignore[no-any-unimported]
    """Voltage analog-input channel.

    Maps to ``Task.ai_channels.add_ai_voltage_chan`` on the NI side.

    Attributes:
        min_val: Lower limit of the expected input range, in volts.
        max_val: Upper limit of the expected input range, in volts. The NI
            driver uses the (min, max) range to select the most appropriate
            on-board gain.
        terminal_config: Terminal configuration (RSE / NRSE / DIFF /
            PSEUDO_DIFF). ``None`` lets NI pick the device default.
        custom_scale_name: Optional name of a pre-configured custom scale
            registered in MAX. When set, ``min_val``/``max_val`` are scaled
            engineering units, not volts.
    """

    kind: ClassVar[str] = "ai_voltage"
    min_val: float = -10.0
    max_val: float = 10.0
    # The trailing ``type: ignore`` is required: mypy follows-imports=skip on
    # nidaqmx, so ``TerminalConfiguration`` resolves to ``Any`` and trips
    # ``disallow_any_unimported``. The boundary is intentional.
    terminal_config: TerminalConfiguration | None = None  # type: ignore[no-any-unimported]
    custom_scale_name: str | None = None

    def __post_init__(self) -> None:
        """Validate the voltage range."""
        ChannelSpec.__post_init__(self)
        if self.min_val >= self.max_val:
            raise NIDaqValidationError(
                f"min_val must be < max_val for {self.display_name!r}; "
                f"got {self.min_val!r} >= {self.max_val!r}"
            )


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class ThermocoupleInput(ChannelSpec):  # type: ignore[no-any-unimported]
    """Thermocouple analog-input channel.

    Maps to ``Task.ai_channels.add_ai_thrmcpl_chan`` on the NI side. The
    enum-typed fields are stored as ``int`` values matching
    ``nidaqmx.constants`` so that ``to_dict``/``from_dict`` round-trips through
    JSON without dragging NI's enum machinery into the serialisation layer.

    Attributes:
        thermocouple_type: One of ``nidaqmx.constants.ThermocoupleType`` (J,
            K, T, ...). Required; no sane default.
        min_val: Lower limit of the expected temperature, in ``units``.
        max_val: Upper limit of the expected temperature, in ``units``.
        cjc_source: Cold-junction compensation source. ``None`` lets NI pick
            the device default (typically built-in).
        cjc_val: Cold-junction reference temperature, in ``units``. Only
            relevant for ``CJCSource.CONSTANT_USER_VALUE``.
        units: Temperature units for ``min_val`` / ``max_val`` and the
            returned data. Defaults to degrees Celsius.
    """

    kind: ClassVar[str] = "thermocouple"
    thermocouple_type: ThermocoupleType  # type: ignore[no-any-unimported]
    min_val: float
    max_val: float
    cjc_source: CJCSource | None = None  # type: ignore[no-any-unimported]
    cjc_val: float | None = None
    units: TemperatureUnits = field(  # type: ignore[no-any-unimported]
        default_factory=_default_temperature_units
    )

    def __post_init__(self) -> None:
        """Validate the temperature range."""
        ChannelSpec.__post_init__(self)
        if self.min_val >= self.max_val:
            raise NIDaqValidationError(
                f"min_val must be < max_val for {self.display_name!r}; "
                f"got {self.min_val!r} >= {self.max_val!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise via the enums' ``.value`` so the payload is JSON-encodable.

        ``nidaqmx.constants`` enums do not inherit from :class:`int`, so we
        record ``.value`` (an int) and reconstruct the enum on
        :meth:`from_dict`.
        """
        # Direct call (not super()) — the @dataclass(slots=True) decorator
        # rewrites the class, which leaves super()'s __class__ cell pointing
        # at a now-unrelated class object.
        payload = ChannelSpec.to_dict(self)
        payload["thermocouple_type"] = self.thermocouple_type.value
        payload["cjc_source"] = self.cjc_source.value if self.cjc_source is not None else None
        payload["units"] = self.units.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Reconstruct, restoring enum members from their ``.value`` ints."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        from nidaqmx.constants import (  # noqa: PLC0415
            CJCSource,
            TemperatureUnits,
            ThermocoupleType,
        )

        payload = {k: v for k, v in data.items() if k != "kind"}
        try:
            payload["thermocouple_type"] = ThermocoupleType(payload["thermocouple_type"])
        except (KeyError, ValueError) as exc:
            raise NIDaqValidationError(
                f"unknown ThermocoupleType {payload.get('thermocouple_type')!r}"
            ) from exc
        if payload.get("cjc_source") is not None:
            try:
                payload["cjc_source"] = CJCSource(payload["cjc_source"])
            except ValueError as exc:
                raise NIDaqValidationError(
                    f"unknown CJCSource {payload.get('cjc_source')!r}"
                ) from exc
        units_raw = payload.get("units", TemperatureUnits.DEG_C.value)
        try:
            payload["units"] = TemperatureUnits(units_raw)
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown TemperatureUnits {units_raw!r}") from exc
        return cls(**payload)


__all__ = ["AnalogInputVoltage", "ThermocoupleInput"]
