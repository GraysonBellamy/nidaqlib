"""Analog-input channel specifications.

Includes a shared :class:`AnalogInputBase` that carries the per-channel
ADC-timing knob (``ai_adc_timing_mode`` on the NI side), plus the two
concrete subclasses :class:`AnalogInputVoltage` and
:class:`ThermocoupleInput`. Design doc §8.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Self

from nidaqlib.channels.base import ChannelSpec, register_channel_kind
from nidaqlib.errors import NIDaqValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nidaqmx.constants import (
        ADCTimingMode,
        AutoZeroType,
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


def _coerce_adc_timing_mode(raw: object) -> Any:
    """Restore an :class:`ADCTimingMode` from its serialised ``.value`` int.

    Lazy NI import; re-raises bogus values as :class:`NIDaqValidationError`
    so callers see the standard library error type rather than the NI one.
    """
    from nidaqmx.constants import ADCTimingMode  # noqa: PLC0415

    try:
        return ADCTimingMode(raw)
    except ValueError as exc:
        raise NIDaqValidationError(f"unknown ADCTimingMode {raw!r}") from exc


def _coerce_auto_zero_mode(raw: object) -> Any:
    """Restore an :class:`AutoZeroType` from its serialised ``.value`` int."""
    from nidaqmx.constants import AutoZeroType  # noqa: PLC0415

    try:
        return AutoZeroType(raw)
    except ValueError as exc:
        raise NIDaqValidationError(f"unknown AutoZeroType {raw!r}") from exc


def _coerce_terminal_config(raw: object) -> Any:
    """Restore a :class:`TerminalConfiguration` from its serialised ``.value`` int."""
    from nidaqmx.constants import TerminalConfiguration  # noqa: PLC0415

    try:
        return TerminalConfiguration(raw)
    except ValueError as exc:
        raise NIDaqValidationError(f"unknown TerminalConfiguration {raw!r}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputBase(ChannelSpec):  # type: ignore[no-any-unimported]
    """Shared base for analog-input channel specs.

    Carries the per-channel knobs NI exposes as channel properties on the
    object returned by ``add_ai_*_chan(...)`` — currently ADC timing mode
    and auto-zero mode. Hardware support is module-specific: NI surfaces
    unsupported attributes as a ``DaqError`` at set time, which the
    backend re-raises as :class:`~nidaqlib.errors.NIDaqBackendError`.

    Attributes:
        adc_timing_mode: One of :class:`nidaqmx.constants.ADCTimingMode`,
            or ``None`` to leave the device default in place. Trades
            conversion rate against resolution and configures
            line-frequency rejection on delta-sigma modules. Choose
            ``HIGH_RESOLUTION`` for slow / high-precision work,
            ``HIGH_SPEED`` for throughput, ``BEST_50_HZ_REJECTION`` /
            ``BEST_60_HZ_REJECTION`` to suppress mains-frequency hum, or
            ``CUSTOM`` to address a device-specific timing mode via
            :attr:`adc_custom_timing_mode`.
        adc_custom_timing_mode: Device-specific integer code, only
            meaningful when ``adc_timing_mode is ADCTimingMode.CUSTOM``.
            Required in that case; rejected otherwise.
        auto_zero_mode: One of :class:`nidaqmx.constants.AutoZeroType`,
            or ``None`` to leave the device default in place. ``ONCE``
            performs a single auto-zero at acquisition start (the most
            common useful setting); ``EVERY_SAMPLE`` autozeros each
            conversion at the cost of throughput; ``NONE`` skips
            auto-zero entirely.
    """

    # The trailing ``type: ignore`` is required: mypy follows-imports=skip
    # on nidaqmx, so the enum resolves to ``Any`` and trips
    # ``disallow_any_unimported``. The boundary is intentional.
    adc_timing_mode: ADCTimingMode | None = None  # type: ignore[no-any-unimported]
    adc_custom_timing_mode: int | None = None
    auto_zero_mode: AutoZeroType | None = None  # type: ignore[no-any-unimported]

    def __post_init__(self) -> None:
        """Validate the ADC-timing pairing on top of the base channel checks."""
        # Direct call (not super()) — the @dataclass(slots=True) decorator
        # rewrites the class, which leaves super()'s __class__ cell pointing
        # at a now-unrelated class object.
        ChannelSpec.__post_init__(self)
        from nidaqmx.constants import ADCTimingMode  # noqa: PLC0415

        is_custom = self.adc_timing_mode is ADCTimingMode.CUSTOM
        if self.adc_custom_timing_mode is not None and not is_custom:
            raise NIDaqValidationError(
                f"adc_custom_timing_mode is only valid with "
                f"adc_timing_mode=ADCTimingMode.CUSTOM on {self.display_name!r}; "
                f"got adc_timing_mode={self.adc_timing_mode!r}"
            )
        if is_custom and self.adc_custom_timing_mode is None:
            raise NIDaqValidationError(
                f"adc_timing_mode=ADCTimingMode.CUSTOM requires adc_custom_timing_mode "
                f"on {self.display_name!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise via each enum's ``.value`` so the payload is JSON-encodable."""
        # Direct call (not super()) — see __post_init__ note.
        payload = ChannelSpec.to_dict(self)
        payload["adc_timing_mode"] = (
            self.adc_timing_mode.value if self.adc_timing_mode is not None else None
        )
        payload["auto_zero_mode"] = (
            self.auto_zero_mode.value if self.auto_zero_mode is not None else None
        )
        return payload

    @staticmethod
    def _restore_base_enums(payload: dict[str, Any]) -> None:
        """Mutate ``payload`` in place: swap each AI-base enum ``int`` → enum member.

        Concrete subclasses call this from their :meth:`from_dict` before
        feeding ``payload`` into the dataclass constructor.
        """
        timing_raw = payload.get("adc_timing_mode")
        if timing_raw is not None:
            payload["adc_timing_mode"] = _coerce_adc_timing_mode(timing_raw)
        autozero_raw = payload.get("auto_zero_mode")
        if autozero_raw is not None:
            payload["auto_zero_mode"] = _coerce_auto_zero_mode(autozero_raw)


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputVoltage(AnalogInputBase):  # type: ignore[no-any-unimported]
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

    Inherits :attr:`adc_timing_mode` and :attr:`adc_custom_timing_mode`
    from :class:`AnalogInputBase`.
    """

    kind: ClassVar[str] = "ai_voltage"
    min_val: float = -10.0
    max_val: float = 10.0
    terminal_config: TerminalConfiguration | None = None  # type: ignore[no-any-unimported]
    custom_scale_name: str | None = None

    def __post_init__(self) -> None:
        """Validate the voltage range."""
        AnalogInputBase.__post_init__(self)
        if self.min_val >= self.max_val:
            raise NIDaqValidationError(
                f"min_val must be < max_val for {self.display_name!r}; "
                f"got {self.min_val!r} >= {self.max_val!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise, encoding ``terminal_config`` via its ``.value`` int."""
        payload = AnalogInputBase.to_dict(self)
        payload["terminal_config"] = (
            self.terminal_config.value if self.terminal_config is not None else None
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Reconstruct, restoring enum members from their serialised ``.value`` ints."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        payload = {k: v for k, v in data.items() if k != "kind"}
        AnalogInputBase._restore_base_enums(payload)
        if payload.get("terminal_config") is not None:
            payload["terminal_config"] = _coerce_terminal_config(payload["terminal_config"])
        return cls(**payload)


@register_channel_kind
@dataclass(frozen=True, slots=True, kw_only=True)
class ThermocoupleInput(AnalogInputBase):  # type: ignore[no-any-unimported]
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

    Inherits :attr:`adc_timing_mode` and :attr:`adc_custom_timing_mode`
    from :class:`AnalogInputBase` — most useful here, since the NI 9213 /
    9214 thermocouple modules expose the full set of timing modes.
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
        AnalogInputBase.__post_init__(self)
        if self.min_val >= self.max_val:
            raise NIDaqValidationError(
                f"min_val must be < max_val for {self.display_name!r}; "
                f"got {self.min_val!r} >= {self.max_val!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise, encoding TC-specific enums via their ``.value`` ints."""
        payload = AnalogInputBase.to_dict(self)
        payload["thermocouple_type"] = self.thermocouple_type.value
        payload["cjc_source"] = self.cjc_source.value if self.cjc_source is not None else None
        payload["units"] = self.units.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Reconstruct, restoring enum members from their serialised ``.value`` ints."""
        kind = data.get("kind")
        if kind != cls.kind:
            raise NIDaqValidationError(f"kind mismatch: expected {cls.kind!r}, got {kind!r}")
        from nidaqmx.constants import (  # noqa: PLC0415
            CJCSource,
            TemperatureUnits,
            ThermocoupleType,
        )

        payload = {k: v for k, v in data.items() if k != "kind"}
        AnalogInputBase._restore_base_enums(payload)
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


__all__ = ["AnalogInputBase", "AnalogInputVoltage", "ThermocoupleInput"]
