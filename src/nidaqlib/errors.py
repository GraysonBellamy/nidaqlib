"""Typed exception hierarchy for :mod:`nidaqlib`.

Every library exception inherits from :class:`NIDaqError` and carries a
structured :class:`ErrorContext` describing the failing operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping


_EMPTY_EXTRA: Mapping[str, Any] = MappingProxyType({})


def _empty_extra() -> Mapping[str, Any]:
    return _EMPTY_EXTRA


class ProtocolKind(StrEnum):
    """Wire protocol kind for cross-library :class:`ErrorContext` symmetry.

    Has no members because NI DAQmx is not a wire-protocol library — every
    NI error context carries ``protocol=None``. The type exists for shape
    parity with sibling libs (``alicatlib``, ``sartoriuslib``, ``watlowlib``)
    whose ``ProtocolKind`` enums name real serial / MODBUS / RS-485
    variants.
    """


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Structured context attached to every :class:`NIDaqError`.

    Base fields are shared across the sibling libraries so cross-instrument
    log readers can join exceptions on a common shape. NI extras
    (``task_name``, ``physical_channel``, ``ni_error_code``) sit alongside.

    ``extra`` accepts any ``Mapping`` and is always frozen into a read-only
    :class:`types.MappingProxyType` at construction so the shared empty
    sentinel can never be mutated through ``error.context.extra[k] = v``.

    Base fields (shape-shared with sibling libs):
        port: NI device name (``Dev1``, ``cDAQ1Mod3``), or ``None``.
        address: Always ``None`` for NI (no multi-drop address concept).
        command_name: Logical operation name (``"read"``, ``"start"``,
            ``"configure_timing"``, ...). The unified name; sibling libs
            also call this ``command_name``.
        protocol: Always ``None`` for NI (no wire protocol).
        extra: Free-form additional context.

    NI extras:
        task_name: ``TaskSpec.name`` of the task at fault.
        channel_name: Display name of the at-fault channel (optional).
        physical_channel: NI physical-channel string (e.g. ``Dev1/ai0``).
        ni_error_code: NI DAQmx error code, when known.
    """

    port: str | None = None
    address: str | int | None = None
    command_name: str | None = None
    protocol: ProtocolKind | None = None
    task_name: str | None = None
    channel_name: str | None = None
    physical_channel: str | None = None
    ni_error_code: int | None = None
    extra: Mapping[str, Any] = field(default_factory=_empty_extra)

    def __post_init__(self) -> None:
        if not isinstance(self.extra, MappingProxyType):
            object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    def merged(self, **updates: Any) -> Self:
        """Return a new context with ``updates`` overlaid. Unknown keys go to ``extra``."""
        known: dict[str, Any] = {}
        extra_updates: dict[str, Any] = {}
        for key, value in updates.items():
            if key in _CONTEXT_KNOWN_FIELDS:
                known[key] = value
            else:
                extra_updates[key] = value

        new_extra: Mapping[str, Any] = (
            MappingProxyType({**self.extra, **extra_updates}) if extra_updates else self.extra
        )
        return replace(self, **known, extra=new_extra)


_CONTEXT_KNOWN_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(ErrorContext) if f.name != "extra"
)


_EMPTY_CONTEXT = ErrorContext()


class NIDaqError(Exception):
    """Base class for every :mod:`nidaqlib` exception.

    Carries a typed :class:`ErrorContext`. The ``message`` is the human-readable
    summary; the context is the machine-readable detail.
    """

    context: ErrorContext

    def __init__(self, message: str = "", *, context: ErrorContext | None = None) -> None:
        """Initialise with a human-readable message and optional context.

        Args:
            message: Short, human-readable summary suitable for logs.
            context: Structured fields about the failing operation. ``None``
                yields an empty :class:`ErrorContext`.
        """
        super().__init__(message)
        self.context = context if context is not None else _EMPTY_CONTEXT

    def with_context(self, **updates: Any) -> Self:
        """Return a copy of this error with its context updated.

        Useful when an inner layer raises and an outer layer wants to enrich
        the context (for instance adding ``task_name`` or ``operation``).
        """
        cls = type(self)
        new = cls.__new__(cls)
        new.args = self.args
        try:
            new.__dict__.update(self.__dict__)
        except AttributeError:  # pragma: no cover — no slotted subclass today
            for slot in getattr(cls, "__slots__", ()):
                if hasattr(self, slot):
                    object.__setattr__(new, slot, getattr(self, slot))
        new.context = self.context.merged(**updates)
        new.__cause__ = self.__cause__
        new.__context__ = self.__context__
        new.__traceback__ = self.__traceback__
        return new

    def __str__(self) -> str:
        base = super().__str__()
        ctx = self.context
        bits: list[str] = []
        if ctx.port is not None:
            bits.append(f"port={ctx.port}")
        if ctx.task_name is not None:
            bits.append(f"task={ctx.task_name}")
        if ctx.channel_name is not None:
            bits.append(f"channel={ctx.channel_name}")
        if ctx.physical_channel is not None:
            bits.append(f"physical={ctx.physical_channel}")
        if ctx.command_name is not None:
            bits.append(f"cmd={ctx.command_name}")
        if ctx.ni_error_code is not None:
            bits.append(f"ni_error_code={ctx.ni_error_code}")
        if ctx.extra:
            bits.append(f"extra={dict(ctx.extra)!r}")
        return f"{base} [{', '.join(bits)}]" if bits else base


# --- Configuration / validation ----------------------------------------------


class NIDaqConfigurationError(NIDaqError):
    """Configuration-level error (bad spec, missing required field, ...)."""


class NIDaqValidationError(NIDaqConfigurationError):
    """Request validation failed before any I/O."""


class NIDaqConfirmationRequiredError(NIDaqConfigurationError):
    """A safety-gated start was attempted without ``confirm=True``.

    Raised by :func:`open_device` and :meth:`DaqManager.start` when a task
    that drives hardware (counter pulse outputs, analog outputs) is started
    without the explicit ``confirm=True`` opt-in. See design §5.10 (safe-start
    gate) and the ecosystem ``ConfirmationRequiredError`` convention shared
    with :mod:`watlowlib` and :mod:`sartoriuslib`.
    """


# --- Lifecycle ---------------------------------------------------------------


class NIDaqTaskStateError(NIDaqError):
    """Operation invalid for the task's current lifecycle state.

    Raised, for example, by :meth:`DaqSession.poll` when the task is buffered
    and started — two consumers on the same NI buffer would race.
    """


# --- I/O ---------------------------------------------------------------------


class NIDaqReadError(NIDaqError):
    """A read against the underlying NI task failed."""


class NIDaqWriteError(NIDaqError):
    """A write against the underlying NI task failed.

    Raised by :meth:`DaqSession.write` when the backend rejects the write.
    Out-of-range values fail earlier as :class:`NIDaqValidationError`.
    """


class NIDaqTimeoutError(NIDaqError):
    """An NI read or write exceeded its configured timeout.

    Distinct from :class:`NIDaqTransientError`: this is a hard timeout that
    means the operation gave up. Transient errors mean "retry safe."
    """


class NIDaqTransientError(NIDaqError):
    """A driver-layer error that is safe to retry without rebuilding the task.

    Surfaced by the backend when an NI DAQmx call fails with a code in the
    documented "retry-safe" set (see
    :data:`nidaqlib.backend.nidaqmx_backend._TRANSIENT_NI_CODES`). Common
    examples: buffer-overrun under ``ErrorPolicy.RETURN`` and the
    "samples still arriving" code that NI returns when a read window slid
    just ahead of the producer.
    """


class NIDaqConnectionError(NIDaqError):
    """Communication with the NI backend was lost or could not be established.

    Aligns with the ecosystem ``ConnectionError`` convention (matching
    :class:`watlowlib.WatlowConnectionError`,
    :class:`alicatlib.AlicatConnectionError`,
    :class:`sartoriuslib.SartoriusConnectionError`). NI's backend rarely
    distinguishes "connection lost" from generic backend errors at the
    driver layer; this class is the family seam for those that do.
    """


# --- Resource conflicts ------------------------------------------------------


class NIDaqResourceError(NIDaqError):
    """A physical-channel conflict was detected by the manager preflight.

    Best-effort signal — NI is the final authority. Raised by
    :meth:`DaqManager.add` when the new task's channels overlap with one
    already managed; ``ErrorContext.extra`` carries the conflicting task names
    under ``"conflicts"``.
    """


# --- Backend / dependency ----------------------------------------------------


class NIDaqBackendError(NIDaqError):
    """The backend rejected an operation or surfaced a generic NI failure.

    Used when the failure is not a clean fit for the more specific subclasses
    (read, timeout, validation, state). Wraps :class:`nidaqmx.errors.DaqError`
    via ``__cause__``.
    """


class NIDaqDependencyError(NIDaqError):
    """A required dependency (driver, optional extra) is unavailable."""


# --- Sinks -------------------------------------------------------------------


class NIDaqSinkError(NIDaqError):
    """Base class for sink-layer failures."""


class NIDaqSinkSchemaError(NIDaqSinkError):
    """A sink rejected an input record's shape.

    Most commonly raised by row-oriented sinks (``CsvSink``, ``JsonlSink``)
    when handed a :class:`~nidaqlib.tasks.DaqBlock` without
    ``accept_blocks=True`` — silently scalarising would surprise users with
    1-GB CSV files at 10 kHz × 8 channels (design doc §14.1).
    """


class NIDaqSinkWriteError(NIDaqSinkError):
    """A sink failed while writing a batch (file I/O, DB error, ...)."""


class NIDaqSinkDependencyError(NIDaqSinkError):
    """A sink's optional dependency (``pyarrow``, ``asyncpg``, ...) is missing."""


__all__ = [
    "ErrorContext",
    "NIDaqBackendError",
    "NIDaqConfigurationError",
    "NIDaqConfirmationRequiredError",
    "NIDaqConnectionError",
    "NIDaqDependencyError",
    "NIDaqError",
    "NIDaqReadError",
    "NIDaqResourceError",
    "NIDaqSinkDependencyError",
    "NIDaqSinkError",
    "NIDaqSinkSchemaError",
    "NIDaqSinkWriteError",
    "NIDaqTaskStateError",
    "NIDaqTimeoutError",
    "NIDaqValidationError",
    "NIDaqWriteError",
]
