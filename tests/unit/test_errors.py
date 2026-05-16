"""Tests for the error hierarchy and NI error wrapping.

Covers design doc §16.4 — every ``nidaqmx.errors.DaqError`` raised by the
backend must surface as an :class:`~nidaqlib.errors.NIDaqError` subclass with
the original exception preserved as ``__cause__`` and an
:class:`~nidaqlib.errors.ErrorContext` carrying the operation, task name,
and NI error code.
"""

from __future__ import annotations

import nidaqmx
import nidaqmx.errors
import pytest

from nidaqlib import (
    NIDaqBackendError,
    NIDaqError,
    NIDaqReadError,
    NIDaqTimeoutError,
    NIDaqTransientError,
)
from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend


def test_hierarchy_root() -> None:
    assert issubclass(NIDaqReadError, NIDaqError)
    assert issubclass(NIDaqTimeoutError, NIDaqError)
    assert issubclass(NIDaqTransientError, NIDaqError)
    assert issubclass(NIDaqBackendError, NIDaqError)


def test_error_carries_context() -> None:
    err = NIDaqError("boom")
    # Default context — no task, no operation, but the attribute is present.
    assert err.context is not None
    assert err.context.task_name is None


class _FakeNiDaqError(nidaqmx.errors.DaqError):  # type: ignore[misc, no-any-unimported]
    """A constructible NI error for the wrapping test.

    ``nidaqmx.errors.DaqError`` insists on (message, error_code) — we keep
    that shape so the wrapping code under test sees the same interface.
    """

    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(message, error_code)


class _FakeTask:
    """Minimal task surface used by NidaqmxBackend.read_block on error.

    We only need ``name``, ``in_stream``, and ``number_of_channels`` for the
    error path to be reached; the read itself is short-circuited by a
    monkeypatched ``AnalogMultiChannelReader``.
    """

    name = "ai_test"
    number_of_channels = 1
    in_stream = object()


def test_read_block_wraps_samples_not_yet_available_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NI's -200284 (``SamplesNotYetAvailable``) surfaces as :class:`NIDaqTransientError`.

    Previously this code routed through :class:`NIDaqTimeoutError`; the new
    classification reflects its real meaning ("samples still arriving, retry").
    """
    backend = NidaqmxBackend()
    fake_exc = _FakeNiDaqError("samples not yet available", error_code=-200284)

    class _StubReader:
        def __init__(self, _stream: object) -> None:
            pass

        def read_many_sample(self, *_args: object, **_kwargs: object) -> None:
            raise fake_exc

    monkeypatch.setattr(
        "nidaqmx.stream_readers.AnalogMultiChannelReader",
        _StubReader,
    )

    with pytest.raises(NIDaqTransientError) as exc_info:
        backend.read_block(_FakeTask(), samples_per_channel=10, timeout=0.1)

    raised = exc_info.value
    assert raised.__cause__ is fake_exc
    assert raised.context.task_name == "ai_test"
    assert raised.context.command_name == "read_block"
    assert raised.context.ni_error_code == -200284
    # -200284 must NOT also be classified as NIDaqTimeoutError (one classifier per code).
    assert not isinstance(raised, NIDaqTimeoutError)


def test_read_block_wraps_buffer_overrun_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NI's -200279 (buffer overrun) is also retry-safe → :class:`NIDaqTransientError`."""
    backend = NidaqmxBackend()
    fake_exc = _FakeNiDaqError("buffer overrun", error_code=-200279)

    class _StubReader:
        def __init__(self, _stream: object) -> None:
            pass

        def read_many_sample(self, *_args: object, **_kwargs: object) -> None:
            raise fake_exc

    monkeypatch.setattr(
        "nidaqmx.stream_readers.AnalogMultiChannelReader",
        _StubReader,
    )

    with pytest.raises(NIDaqTransientError) as exc_info:
        backend.read_block(_FakeTask(), samples_per_channel=10, timeout=0.1)

    raised = exc_info.value
    assert raised.context.ni_error_code == -200279


def test_read_block_wraps_generic_error_with_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-timeout NI error surfaces as :class:`NIDaqReadError`."""
    backend = NidaqmxBackend()
    fake_exc = _FakeNiDaqError("device offline", error_code=-1)

    class _StubReader:
        def __init__(self, _stream: object) -> None:
            pass

        def read_many_sample(self, *_args: object, **_kwargs: object) -> None:
            raise fake_exc

    monkeypatch.setattr(
        "nidaqmx.stream_readers.AnalogMultiChannelReader",
        _StubReader,
    )

    with pytest.raises(NIDaqReadError) as exc_info:
        backend.read_block(_FakeTask(), samples_per_channel=10, timeout=0.1)

    raised = exc_info.value
    assert raised.__cause__ is fake_exc
    assert raised.context.ni_error_code == -1
    assert raised.context.command_name == "read_block"
