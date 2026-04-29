"""Deterministic in-memory backend for tests and examples.

There is no transport-level seam in DAQ (no bytes on the wire), so the fake
substitution point lives at :class:`~nidaqlib.backend.base.DaqBackend`.
:class:`FakeDaqBackend` is the test-double for everything below the session
layer: it simulates task lifecycle, scripted block reads, and the
every-N-samples buffer-event callback that the §11.3.2 driver-thread bridge
consumes.

See design doc §10.4 and Appendix C.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from nidaqlib.errors import (
    ErrorContext,
    NIDaqBackendError,
    NIDaqConfigurationError,
    NIDaqReadError,
    NIDaqTimeoutError,
    NIDaqWriteError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from nidaqlib.channels.base import ChannelSpec
    from nidaqlib.system.models import DeviceInfo
    from nidaqlib.tasks.spec import TdmsLogging, Timing
    from nidaqlib.tasks.triggers import TriggerSpec


def _empty_channels() -> list[ChannelSpec]:
    return []


def _empty_writes() -> list[dict[str, float | bool]]:
    return []


@dataclass
class _FakeTask:
    """Internal handle returned by :meth:`FakeDaqBackend.create_task`.

    Tests rarely interact with this directly; they read
    :attr:`FakeDaqBackend.operations` to assert ordering invariants, or
    :attr:`FakeDaqBackend.writes` for write-side assertions.
    """

    name: str
    channels: list[ChannelSpec] = field(default_factory=_empty_channels)
    timing: Timing | None = None
    logging: TdmsLogging | None = None
    trigger: TriggerSpec | None = None
    started: bool = False
    closed: bool = False
    callback: Callable[[int], None] | None = None
    callback_n: int = 0
    writes: list[dict[str, float | bool]] = field(default_factory=_empty_writes)
    last_write: dict[str, float | bool] | None = None


@dataclass(frozen=True, slots=True)
class _Operation:
    """One recorded backend operation, used by tests for ordering assertions."""

    op: str
    task_name: str | None
    detail: str | None = None


class FakeDaqBackend:
    """In-memory test double for :class:`~nidaqlib.backend.base.DaqBackend`.

    Capabilities:

    - Scripted block reads, keyed by task name and consumed FIFO.
    - Optional deterministic ramp generation when no script is provided.
    - Scripted timeouts / errors injected by the test.
    - An operation log (:attr:`operations`) for asserting the §11.3.2
      shutdown ordering.
    - A driver-thread simulator (:meth:`simulate_callbacks`) that fires the
      registered every-N-samples callback on a private ``threading.Thread``,
      matching the threading model of NI's real callback.
    """

    def __init__(
        self,
        *,
        blocks: dict[str, Sequence[np.ndarray]] | None = None,
        read_block_default_shape: tuple[int, int] | None = None,
        read_errors: dict[str, Iterable[Exception]] | None = None,
        write_errors: dict[str, Iterable[Exception]] | None = None,
    ) -> None:
        """Configure the fake backend.

        Args:
            blocks: Per-task-name sequence of pre-built ``np.ndarray`` blocks
                returned in order from :meth:`read_block`. When exhausted,
                the backend falls back to ``read_block_default_shape`` (or
                raises if neither is set).
            read_block_default_shape: ``(n_channels, samples_per_channel)``
                used to synthesise a deterministic ramp when no scripted
                block is queued. ``None`` means "raise instead of synthesise".
            read_errors: Per-task-name iterable of exceptions to raise from
                :meth:`read_block`. Each entry is consumed by the next read
                call before the scripted blocks queue is consulted.
            write_errors: Per-task-name iterable of exceptions to raise from
                :meth:`write`. Each entry is consumed by the next write
                call before the values are recorded.
        """
        self._blocks: dict[str, list[np.ndarray]] = {
            name: list(seq) for name, seq in (blocks or {}).items()
        }
        self._default_shape = read_block_default_shape
        self._read_errors: dict[str, list[Exception]] = {
            name: list(errs) for name, errs in (read_errors or {}).items()
        }
        self._write_errors: dict[str, list[Exception]] = {
            name: list(errs) for name, errs in (write_errors or {}).items()
        }
        self._tasks: dict[str, _FakeTask] = {}
        self._read_counter: dict[str, int] = defaultdict(int)
        self.operations: list[_Operation] = []
        """Append-only log of backend calls. Tests assert ordering against this."""

        self._sim_threads: list[threading.Thread] = []
        self._device_info: dict[str, DeviceInfo] = {}

    # -- Task lifecycle -------------------------------------------------------

    def create_task(self, name: str) -> _FakeTask:
        """Create and return a new :class:`_FakeTask`.

        Raises:
            NIDaqBackendError: A task with ``name`` already exists.
        """
        if name in self._tasks:
            raise NIDaqBackendError(
                f"task {name!r} already exists",
                context=ErrorContext(task_name=name, operation="create_task"),
            )
        task = _FakeTask(name=name)
        self._tasks[name] = task
        self.operations.append(_Operation("create_task", name))
        return task

    def close_task(self, task: _FakeTask) -> None:
        """Mark ``task`` closed. Idempotent."""
        if task.closed:
            return
        task.closed = True
        self.operations.append(_Operation("close_task", task.name))

    def add_channel(self, task: _FakeTask, spec: ChannelSpec) -> None:
        """Append ``spec`` to ``task.channels``."""
        task.channels.append(spec)
        self.operations.append(_Operation("add_channel", task.name, spec.physical_channel))

    def configure_timing(self, task: _FakeTask, timing: Timing) -> None:
        """Record ``timing`` on ``task``."""
        task.timing = timing
        self.operations.append(
            _Operation("configure_timing", task.name, f"rate_hz={timing.rate_hz}")
        )

    def configure_logging(self, task: _FakeTask, logging: TdmsLogging) -> None:
        """Record ``logging`` on ``task``."""
        task.logging = logging
        self.operations.append(_Operation("configure_logging", task.name, f"path={logging.path!s}"))

    def configure_trigger(self, task: _FakeTask, trigger: TriggerSpec) -> None:
        """Record ``trigger`` on ``task`` for test inspection."""
        task.trigger = trigger
        detail = f"kind={trigger.kind};source={trigger.source}"
        self.operations.append(_Operation("configure_trigger", task.name, detail))

    def start_task(self, task: _FakeTask) -> None:
        """Mark ``task`` started."""
        task.started = True
        self.operations.append(_Operation("start_task", task.name))

    def stop_task(self, task: _FakeTask) -> None:
        """Mark ``task`` stopped."""
        task.started = False
        self.operations.append(_Operation("stop_task", task.name))

    # -- Reads ---------------------------------------------------------------

    def read_block(
        self,
        task: _FakeTask,
        samples_per_channel: int,
        timeout: float,
    ) -> np.ndarray:
        """Pop the next scripted block, fall back to a deterministic ramp.

        Raises:
            NIDaqTimeoutError: A scripted timeout exception was queued.
            NIDaqReadError: A scripted read exception was queued, or the
                queue is empty and no ``read_block_default_shape`` is set.
        """
        del timeout  # The fake never blocks — timeout is metadata for tests.
        errs = self._read_errors.get(task.name)
        if errs:
            err = errs.pop(0)
            self.operations.append(_Operation("read_block_error", task.name, str(err)))
            if isinstance(err, (NIDaqReadError, NIDaqTimeoutError)):
                raise err
            raise NIDaqReadError(
                f"scripted read error: {err!r}",
                context=ErrorContext(task_name=task.name, operation="read_block"),
            ) from err
        scripted = self._blocks.get(task.name)
        if scripted:
            block = scripted.pop(0)
        elif self._default_shape is not None:
            n_channels, _ = self._default_shape
            i = self._read_counter[task.name]
            block = np.full(
                (n_channels, samples_per_channel),
                fill_value=float(i),
                dtype=np.float64,
            )
            self._read_counter[task.name] = i + 1
        else:
            raise NIDaqReadError(
                f"no scripted blocks remain for task {task.name!r}",
                context=ErrorContext(task_name=task.name, operation="read_block"),
            )
        if block.shape[1] != samples_per_channel:
            # Reshape on the fly for tests that pre-build a long stream and
            # let the recorder choose the chunk size.
            block = block[:, :samples_per_channel]
        self.operations.append(_Operation("read_block", task.name, str(block.shape)))
        return block

    # -- Writes --------------------------------------------------------------

    def write(
        self,
        task: _FakeTask,
        values: Mapping[str, float | bool],
        timeout: float,
    ) -> None:
        """Record one write — for tests asserting on outputs.

        Validation parity with the real backend: missing channel keys raise
        :class:`NIDaqConfigurationError`; scripted errors raise from the
        per-task ``write_errors`` queue.

        Raises:
            NIDaqConfigurationError: ``values`` is missing entries for one
                or more output channels.
            NIDaqWriteError / NIDaqTimeoutError: A scripted error was queued.
        """
        del timeout  # The fake never blocks — timeout is metadata for tests.
        from nidaqlib.channels.analog_output import AnalogOutputVoltage  # noqa: PLC0415
        from nidaqlib.channels.digital_output import DigitalOutput  # noqa: PLC0415

        output_channels = [
            ch for ch in task.channels if isinstance(ch, (AnalogOutputVoltage, DigitalOutput))
        ]
        if not output_channels:
            raise NIDaqConfigurationError(
                "task has no writable channels (AO or DO)",
                context=ErrorContext(task_name=task.name, operation="write"),
            )
        names = [ch.display_name for ch in output_channels]
        missing = [n for n in names if n not in values]
        if missing:
            raise NIDaqConfigurationError(
                f"write missing values for channel(s): {missing!r}",
                context=ErrorContext(task_name=task.name, operation="write"),
            )

        errs = self._write_errors.get(task.name)
        if errs:
            err = errs.pop(0)
            self.operations.append(_Operation("write_error", task.name, str(err)))
            if isinstance(err, (NIDaqWriteError, NIDaqTimeoutError)):
                raise err
            raise NIDaqWriteError(
                f"scripted write error: {err!r}",
                context=ErrorContext(task_name=task.name, operation="write"),
            ) from err

        snapshot: dict[str, float | bool] = {n: values[n] for n in names}
        task.writes.append(snapshot)
        task.last_write = snapshot
        self.operations.append(_Operation("write", task.name, repr(snapshot)))

    # -- Callback bridge -----------------------------------------------------

    def register_every_n_samples(
        self,
        task: _FakeTask,
        n: int,
        callback: Callable[[int], None],
    ) -> _FakeTask:
        """Stash ``callback`` on the task. Returns ``task`` as the handle.

        Mirrors NI's ordering invariant: registration must precede
        ``task.start()``. Real NI rejects post-start registration with
        -200960 ("Register all your DAQmx software events prior to starting
        the task"); the fake raises an analogous
        :class:`NIDaqBackendError` so the unit suite catches violations
        that the hardware would otherwise surface only at integration.

        Raises:
            NIDaqBackendError: A callback is already registered, or the task
                has already been started.
        """
        if task.started:
            raise NIDaqBackendError(
                f"task {task.name!r} is already started; "
                "register_every_n_samples must run before start_task "
                "(NI rejects post-start registration with -200960)",
                context=ErrorContext(task_name=task.name, operation="register_every_n_samples"),
            )
        if task.callback is not None:
            raise NIDaqBackendError(
                f"task {task.name!r} already has a buffer-event callback",
                context=ErrorContext(task_name=task.name, operation="register_every_n_samples"),
            )
        task.callback = callback
        task.callback_n = n
        self.operations.append(_Operation("register_every_n_samples", task.name, f"n={n}"))
        return task

    def unregister_every_n_samples(self, task: _FakeTask, handle: Any) -> None:
        """Clear the buffer-event callback on ``task``.

        Mirrors NI's ordering invariant: unregistration requires the task
        to be stopped. Real NI rejects post-running unregister with -200986
        ("DAQmx software event cannot be unregistered because the task is
        running"); the fake raises an analogous
        :class:`NIDaqBackendError` so the unit suite catches violations
        that real hardware would otherwise surface only at integration.

        Raises:
            NIDaqBackendError: The task is still running.
        """
        del handle
        if task.started:
            raise NIDaqBackendError(
                f"task {task.name!r} is still running; "
                "unregister_every_n_samples must run after stop_task "
                "(NI rejects post-running unregister with -200986)",
                context=ErrorContext(task_name=task.name, operation="unregister_every_n_samples"),
            )
        task.callback = None
        task.callback_n = 0
        self.operations.append(_Operation("unregister_every_n_samples", task.name))

    # -- Discovery / preflight ----------------------------------------------

    def device_info(self, device: str) -> DeviceInfo | None:
        """Return scripted ``DeviceInfo`` for ``device`` if registered, else ``None``.

        Tests register product types via :meth:`register_device_info` so the
        manager's module-level preflight can be exercised against the fake.
        Default behaviour (no registration) returns ``None``, matching the
        Protocol's "unknown device" semantics.
        """
        return self._device_info.get(device)

    def register_device_info(self, device: str, *, product_type: str) -> None:
        """Scripted DeviceInfo for tests of the manager's module-level preflight."""
        from nidaqlib.system.models import DeviceInfo as _DeviceInfo  # noqa: PLC0415

        self._device_info[device] = _DeviceInfo(
            name=device,
            product_type=product_type,
            serial_number=None,
            ai_physical_channels=(),
            ao_physical_channels=(),
            di_lines=(),
            do_lines=(),
            ci_physical_channels=(),
            co_physical_channels=(),
        )

    # -- Test helpers --------------------------------------------------------

    def simulate_callbacks(
        self,
        task: _FakeTask,
        *,
        firings: int,
        cadence_s: float = 0.0,
    ) -> threading.Thread:
        """Fire the registered callback ``firings`` times from a worker thread.

        Models the behaviour of NI's DAQmx driver thread so the §11.3.2
        bridge can be exercised end-to-end in unit tests. The callback runs
        on a fresh ``threading.Thread`` (NOT the asyncio event loop).

        The simulator stops early if the callback is unregistered between
        firings — this models NI's "any pending events are discarded" note
        on ``stop_task``.

        Args:
            task: The fake task on which the callback was registered.
            firings: Number of times to invoke the callback.
            cadence_s: Optional sleep between firings, in seconds. Use 0 for
                a tight burst, > 0 to mimic a finite sample-clock cadence.

        Returns:
            The :class:`threading.Thread` running the simulator. Tests
            usually do not need to ``.join()`` this — the bridge tests rely
            on the recorder's own shutdown to drain pending chunks. Joinable
            if asserting on thread liveness.
        """

        def _run() -> None:
            for _ in range(firings):
                cb = task.callback
                if cb is None:
                    return
                cb(task.callback_n)
                if cadence_s > 0.0:
                    threading.Event().wait(cadence_s)

        thread = threading.Thread(
            target=_run,
            name=f"FakeDaqBackend-cb-sim[{task.name}]",
            daemon=True,
        )
        thread.start()
        self._sim_threads.append(thread)
        return thread


__all__ = ["FakeDaqBackend"]
