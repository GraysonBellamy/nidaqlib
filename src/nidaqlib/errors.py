"""Typed exception hierarchy for :mod:`nidaqlib`.

Every library exception inherits from :class:`NIDaqError` and carries a
structured :class:`ErrorContext` describing the failing operation. See design
doc §16.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _empty_extra() -> dict[str, Any]:
    return {}


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Structured context attached to every :class:`NIDaqError`.

    Fields are best-effort — missing data is ``None`` rather than raising. The
    DAQ-specific keys (``task_name``, ``physical_channel``, ``ni_error_code``)
    let cross-instrument log readers join NIDaq exceptions to the same task /
    channel / error-code identifiers visible from raw ``nidaqmx-python``.
    """

    task_name: str | None = None
    channel_name: str | None = None
    physical_channel: str | None = None
    operation: str | None = None
    ni_error_code: int | None = None
    extra: dict[str, Any] = field(default_factory=_empty_extra)


class NIDaqError(Exception):
    """Base class for every :mod:`nidaqlib` exception."""

    def __init__(self, message: str = "", *, context: ErrorContext | None = None) -> None:
        """Initialise with a human-readable message and optional context.

        Args:
            message: Short, human-readable summary suitable for logs.
            context: Structured fields about the failing operation. ``None``
                yields an empty :class:`ErrorContext`.
        """
        super().__init__(message)
        self.context = context or ErrorContext()


# --- Configuration / validation ----------------------------------------------


class NIDaqConfigurationError(NIDaqError):
    """Configuration-level error (bad spec, missing required field, ...)."""


class NIDaqValidationError(NIDaqConfigurationError):
    """Request validation failed before any I/O."""


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
    """An NI read or write exceeded its configured timeout."""


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
