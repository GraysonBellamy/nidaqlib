"""Task-level configuration types.

A :class:`TaskSpec` is the declarative, serialisable bundle that fully
describes one NI task: its channels, its sample-clock timing, its TDMS
driver-side logging, and its trigger configuration. Design doc §8.1, §8.5,
§14.6.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

from nidaqlib.channels.base import ChannelSpec
from nidaqlib.errors import NIDaqValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from nidaqmx.constants import LoggingMode, LoggingOperation

    from nidaqlib.tasks.triggers import TriggerSpec


class AcquisitionMode(StrEnum):
    """Sample-clock acquisition mode.

    Mirrors a subset of ``nidaqmx.constants.AcquisitionType``. Kept as a
    library-side enum so :class:`TaskSpec` round-trips through JSON without
    pulling NI's enum machinery into the serialisation layer.
    """

    FINITE = "finite"
    CONTINUOUS = "continuous"
    ON_DEMAND = "on_demand"
    """Software-timed; no hardware sample clock is configured."""


class Edge(StrEnum):
    """Active edge for the sample clock or a trigger.

    Mirrors ``nidaqmx.constants.Edge``.
    """

    RISING = "rising"
    FALLING = "falling"


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    """Sample-clock timing configuration.

    Attributes:
        rate_hz: Sample clock rate, in Hz. Required for hardware-timed modes
            (finite / continuous).
        mode: Acquisition mode. Defaults to continuous.
        samples_per_channel: For ``FINITE``, the total number of samples per
            channel. For ``CONTINUOUS``, this sizes the on-board buffer. NI
            chooses a sensible default when ``None``.
        source: Optional sample-clock source terminal (e.g. an external
            terminal name); ``None`` selects the on-board clock.
        active_edge: Sample-clock active edge. Rising by default.
    """

    rate_hz: float
    mode: AcquisitionMode = AcquisitionMode.CONTINUOUS
    samples_per_channel: int | None = None
    source: str | None = None
    active_edge: Edge = Edge.RISING

    def __post_init__(self) -> None:
        """Validate timing parameters before they reach NI."""
        if self.rate_hz <= 0.0:
            raise NIDaqValidationError(f"rate_hz must be > 0, got {self.rate_hz!r}")
        if self.samples_per_channel is not None and self.samples_per_channel <= 0:
            raise NIDaqValidationError(
                f"samples_per_channel must be > 0 when set, got {self.samples_per_channel!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict.

        Enum members serialise to their string values so the result is
        JSON-encodable without a custom encoder.
        """
        return {
            "rate_hz": self.rate_hz,
            "mode": self.mode.value,
            "samples_per_channel": self.samples_per_channel,
            "source": self.source,
            "active_edge": self.active_edge.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Args:
            data: Mapping carrying the timing fields.

        Raises:
            NIDaqValidationError: An enum field carries an unknown value.
        """
        try:
            mode = AcquisitionMode(data.get("mode", AcquisitionMode.CONTINUOUS.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown AcquisitionMode {data.get('mode')!r}") from exc
        try:
            edge = Edge(data.get("active_edge", Edge.RISING.value))
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown Edge {data.get('active_edge')!r}") from exc
        return cls(
            rate_hz=float(data["rate_hz"]),
            mode=mode,
            samples_per_channel=(
                int(data["samples_per_channel"])
                if data.get("samples_per_channel") is not None
                else None
            ),
            source=data.get("source"),
            active_edge=edge,
        )


def _empty_metadata() -> dict[str, str | int | float | bool]:
    return {}


def _default_logging_operation() -> Any:
    """Return :class:`nidaqmx.constants.LoggingOperation.OPEN_OR_CREATE`.

    Lazy import keeps ``nidaqlib.tasks.spec`` importable in contexts where
    ``nidaqmx`` resolves only at call time. Invoked once per
    :class:`TdmsLogging` construction.
    """
    from nidaqmx.constants import LoggingOperation  # noqa: PLC0415

    return LoggingOperation.OPEN_OR_CREATE


def _default_logging_mode() -> Any:
    """Return :class:`nidaqmx.constants.LoggingMode.LOG_AND_READ`.

    ``LOG_AND_READ`` keeps the application read path working — samples flow
    into both the TDMS file and the NI buffer for :class:`DaqBlock` emission.
    Switch to ``LOG`` for write-only TDMS (faster but bypasses the read
    path; the recorder detects this and exits cleanly — see design §14.6).
    """
    from nidaqmx.constants import LoggingMode  # noqa: PLC0415

    return LoggingMode.LOG_AND_READ


@dataclass(frozen=True, slots=True, kw_only=True)
class TdmsLogging:  # type: ignore[no-any-unimported]
    """Driver-side TDMS logging configuration (design doc §14.6).

    Attached to :attr:`TaskSpec.logging`. The wrapper does not write TDMS
    by hand — ``nidaqmx-python`` exposes task-level driver-side logging via
    ``task.in_stream.configure_logging(...)``. ``nidaqlib`` configures the
    knobs and otherwise stays out of the way.

    Attributes:
        path: Destination ``.tdms`` file. Stringified into NI's call.
        operation: How NI handles a pre-existing file. Defaults to
            :class:`nidaqmx.constants.LoggingOperation.OPEN_OR_CREATE`.
        mode: Write-and-read vs. write-only. Defaults to
            :class:`nidaqmx.constants.LoggingMode.LOG_AND_READ`. Choose
            :class:`nidaqmx.constants.LoggingMode.LOG` for a write-only
            stream — :func:`~nidaqlib.streaming.record` detects this and
            emits an empty stream rather than blocking forever in
            ``read_block``.
        group_name: Optional TDMS group name. ``None`` lets NI default.
    """

    path: str | Path
    operation: LoggingOperation = field(  # type: ignore[no-any-unimported]
        default_factory=_default_logging_operation
    )
    mode: LoggingMode = field(  # type: ignore[no-any-unimported]
        default_factory=_default_logging_mode
    )
    group_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict using each enum's ``.value``."""
        return {
            "path": str(self.path),
            "operation": self.operation.value,
            "mode": self.mode.value,
            "group_name": self.group_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise, restoring enum members from their ``.value`` ints."""
        from nidaqmx.constants import (  # noqa: PLC0415
            LoggingMode,
            LoggingOperation,
        )

        op_raw = data.get("operation", LoggingOperation.OPEN_OR_CREATE.value)
        try:
            operation = LoggingOperation(op_raw)
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown LoggingOperation {op_raw!r}") from exc
        mode_raw = data.get("mode", LoggingMode.LOG_AND_READ.value)
        try:
            mode = LoggingMode(mode_raw)
        except ValueError as exc:
            raise NIDaqValidationError(f"unknown LoggingMode {mode_raw!r}") from exc
        return cls(
            path=str(data["path"]),
            operation=operation,
            mode=mode,
            group_name=data.get("group_name"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskSpec:
    """Declarative description of one NI task.

    Attributes:
        name: Task name. Must be unique within an :class:`~nidaqlib.DaqManager`
            and labels :class:`DaqReading` / :class:`DaqBlock` rows.
        channels: One or more :class:`~nidaqlib.channels.ChannelSpec`
            instances. Order is preserved and is the source of truth for
            ``DaqBlock.channels`` row ordering.
        timing: Optional :class:`Timing`. ``None`` means on-demand /
            software-polled.
        trigger: Optional :class:`~nidaqlib.tasks.triggers.TriggerSpec`.
            ``None`` means "start as soon as :meth:`DaqSession.start`
            returns" (NI's default).
        logging: Optional :class:`TdmsLogging` for driver-side TDMS. ``None``
            disables TDMS (the default).
        metadata: Free-form scalar metadata propagated into emitted records.
    """

    name: str
    channels: Sequence[ChannelSpec]
    timing: Timing | None = None
    trigger: TriggerSpec | None = None
    logging: TdmsLogging | None = None
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        """Validate the channel list shape (the cheap, always-true invariants).

        Raises:
            NIDaqValidationError: ``channels`` is empty or contains a
                non-:class:`ChannelSpec` element.
        """
        if len(self.channels) == 0:
            raise NIDaqValidationError(f"TaskSpec {self.name!r}: at least one channel is required")
        if not self.name:
            raise NIDaqValidationError("TaskSpec.name must be a non-empty string")
        channels = tuple(self.channels)
        object.__setattr__(self, "channels", channels)
        for ch in self.channels:
            if not isinstance(ch, ChannelSpec):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise NIDaqValidationError(
                    f"TaskSpec {self.name!r}: channels must be ChannelSpec instances, "
                    f"got {type(ch).__name__}"
                )
        names = [ch.display_name for ch in self.channels]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise NIDaqValidationError(
                f"TaskSpec {self.name!r}: duplicate channel display names {duplicates!r}"
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict, dispatching channels by ``kind``."""
        return {
            "name": self.name,
            "channels": [ch.to_dict() for ch in self.channels],
            "timing": self.timing.to_dict() if self.timing is not None else None,
            "trigger": self.trigger.to_dict() if self.trigger is not None else None,
            "logging": self.logging.to_dict() if self.logging is not None else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Args:
            data: Mapping carrying the task-spec fields.

        Raises:
            NIDaqValidationError: A channel or trigger entry has an unknown
                ``kind``, or required structural fields are malformed.
        """
        from nidaqlib.tasks.triggers import TriggerSpec  # noqa: PLC0415

        timing_payload = data.get("timing")
        timing = Timing.from_dict(timing_payload) if timing_payload is not None else None
        trigger_payload = data.get("trigger")
        if trigger_payload is None:
            trigger = None
        elif isinstance(trigger_payload, Mapping):
            trigger = TriggerSpec.from_dict(trigger_payload)  # pyright: ignore[reportUnknownArgumentType]
        else:
            raise NIDaqValidationError(
                f"TaskSpec.trigger must be a mapping or null, got {type(trigger_payload).__name__}"
            )
        logging_payload = data.get("logging")
        logging = TdmsLogging.from_dict(logging_payload) if logging_payload is not None else None
        raw_channels: object = data.get("channels", [])
        if not isinstance(raw_channels, list):
            raise NIDaqValidationError(
                f"TaskSpec.channels must be a list, got {type(raw_channels).__name__}"
            )
        channels: list[ChannelSpec] = []
        for ch in raw_channels:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(ch, Mapping):
                raise NIDaqValidationError(
                    f"TaskSpec.channels[*] must be a mapping, got {type(ch).__name__}"  # pyright: ignore[reportUnknownArgumentType]
                )
            channels.append(ChannelSpec.from_dict(ch))  # pyright: ignore[reportUnknownArgumentType]
        metadata_raw: object = data.get("metadata", {})
        if not isinstance(metadata_raw, Mapping):
            raise NIDaqValidationError(
                f"TaskSpec.metadata must be a mapping, got {type(metadata_raw).__name__}"
            )
        return cls(
            name=str(data["name"]),
            channels=channels,
            timing=timing,
            trigger=trigger,
            logging=logging,
            metadata=dict(metadata_raw),  # pyright: ignore[reportUnknownArgumentType]
        )

    def replace(self, **updates: Any) -> Self:
        """Return a copy of this spec with ``updates`` applied.

        Mirrors ``dataclasses.replace`` but is exposed as a method for
        consistency with the rest of the API.
        """
        return dataclasses.replace(self, **updates)


__all__ = ["AcquisitionMode", "Edge", "TaskSpec", "TdmsLogging", "Timing"]
