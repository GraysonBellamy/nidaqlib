"""``DaqManager`` — multi-task lifecycle and dispatch (design doc §15).

Direct-port of sartoriuslib's ``manager.py``, shape-translated for DAQ:

- Port-keyed locks → per-task locks plus a per-device lock for tasks that
  share a card (best-effort; NI is the final authority).
- Sibling ``DeviceResult[T]`` → :class:`TaskResult[T]`.
- The recorder consumed :class:`ErrorPolicy` in v0.1; the manager becomes
  the second consumer here.

Lifecycle invariants (sibling parity):

- Sessions start lazily. :meth:`add` constructs a :class:`DaqSession` and
  records the spec; :meth:`start` performs the actual NI calls.
- :meth:`close` unwinds in **LIFO** order (last added, first closed). On
  failure during a group operation, all errors are collected into one
  :class:`ExceptionGroup` rather than aborting on the first.
- :meth:`add` is idempotent on the same name + spec — a duplicate ``add``
  bumps a refcount; the matching :meth:`remove` decrements. Only when the
  refcount reaches zero is the session torn down.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from nidaqlib.errors import (
    ErrorContext,
    NIDaqError,
    NIDaqResourceError,
    NIDaqTaskStateError,
)
from nidaqlib.streaming.block import ErrorPolicy
from nidaqlib.tasks.session import DaqSession

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from types import TracebackType

    from nidaqlib.backend.base import DaqBackend
    from nidaqlib.tasks.models import DaqBlock, DaqReading
    from nidaqlib.tasks.spec import TaskSpec


__all__ = ["DaqManager", "TaskResult"]


@dataclass(frozen=True, slots=True)
class TaskResult[T]:
    """One per-task outcome from a manager group operation.

    Mirrors sibling ``DeviceResult[T]`` but is named for the task-level
    granularity of the DAQ manager (one NI task per slot, not one device
    per slot).

    Attributes:
        name: Manager-add name of the task.
        value: The operation's success value, or ``None`` on error.
        error: The wrapped :class:`NIDaqError`, or ``None`` on success.
    """

    name: str
    value: T | None
    error: NIDaqError | None

    @property
    def ok(self) -> bool:
        """``True`` when the operation succeeded for this task."""
        return self.error is None


def _device_of(physical_channel: str) -> str:
    """Return the NI device prefix of a physical channel string.

    ``"Dev1/ai0"`` → ``"Dev1"``; lines like ``"Dev1/port0/line0"`` →
    ``"Dev1"``. Used for the per-device lock and the preflight conflict
    check.
    """
    return physical_channel.split("/", 1)[0] if "/" in physical_channel else physical_channel


def _channels_of(spec: TaskSpec) -> tuple[str, ...]:
    """Collect the physical-channel strings of ``spec`` for preflight."""
    return tuple(ch.physical_channel for ch in spec.channels)


class DaqManager:
    """Lifecycle, dispatch, and group operations across multiple NI tasks.

    Construction does not touch the driver. Add tasks via :meth:`add`
    (lazy — no NI calls), then call :meth:`start` to bring them up.
    :meth:`close` always tears down in reverse-add order.

    The manager is async-context-manager-aware: ``async with DaqManager()``
    closes every session on exit, even on raised errors.
    """

    def __init__(self, *, error_policy: ErrorPolicy = ErrorPolicy.RAISE) -> None:
        """Create a manager.

        Args:
            error_policy: Default policy for group operations
                (:meth:`start`, :meth:`stop`, :meth:`poll`,
                :meth:`read_block`). :attr:`ErrorPolicy.RAISE` collects
                errors into an :class:`ExceptionGroup`;
                :attr:`ErrorPolicy.RETURN` surfaces them as
                ``TaskResult.error`` rows and continues.
        """
        self._error_policy = error_policy
        self._sessions: dict[str, DaqSession] = {}
        self._specs: dict[str, TaskSpec] = {}
        self._refcounts: dict[str, int] = {}
        self._order: list[str] = []
        self._task_locks: dict[str, anyio.Lock] = {}
        self._device_locks: dict[str, anyio.Lock] = {}
        self._global_lock = anyio.Lock()
        self._closed = False

    # -- Properties ----------------------------------------------------------

    @property
    def names(self) -> tuple[str, ...]:
        """Names of currently managed tasks, in add-order."""
        return tuple(self._order)

    @property
    def is_closed(self) -> bool:
        """``True`` once :meth:`close` has run."""
        return self._closed

    @property
    def error_policy(self) -> ErrorPolicy:
        """The default error policy for group operations."""
        return self._error_policy

    # -- Add / remove --------------------------------------------------------

    async def add(
        self,
        name: str,
        spec: TaskSpec,
        *,
        backend: DaqBackend | None = None,
    ) -> DaqSession:
        """Register a task with this manager. Idempotent on duplicate ``name``.

        Performs a best-effort preflight conflict check against tasks
        already managed (design doc §15.3). NI is the final authority —
        the preflight only catches obvious overlaps.

        Args:
            name: Manager-side label for this task. Must be unique.
            spec: :class:`TaskSpec` describing the task.
            backend: Optional :class:`DaqBackend`. Defaults to
                :class:`NidaqmxBackend` (lazy import).

        Returns:
            The :class:`DaqSession` registered under ``name``. Re-adding
            the same ``(name, spec)`` returns the existing session and
            bumps a refcount.

        Raises:
            NIDaqTaskStateError: ``name`` already maps to a different spec,
                or the manager is closed.
            NIDaqResourceError: ``spec`` overlaps physical channels with
                an already-managed task.
        """
        if self._closed:
            raise NIDaqTaskStateError(
                "DaqManager is closed",
                context=ErrorContext(task_name=name, operation="manager.add"),
            )
        async with self._global_lock:
            existing = self._sessions.get(name)
            if existing is not None:
                if self._specs.get(name) is not spec and self._specs.get(name) != spec:
                    raise NIDaqTaskStateError(
                        f"task {name!r} already registered with a different spec",
                        context=ErrorContext(task_name=name, operation="manager.add"),
                    )
                self._refcounts[name] = self._refcounts.get(name, 1) + 1
                return existing

            self._preflight_conflicts(name, spec)

            if backend is None:
                from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend  # noqa: PLC0415

                backend = NidaqmxBackend()
            session = DaqSession(spec, backend)
            self._sessions[name] = session
            self._specs[name] = spec
            self._refcounts[name] = 1
            self._order.append(name)
            self._task_locks[name] = anyio.Lock()
            for ch in spec.channels:
                dev = _device_of(ch.physical_channel)
                self._device_locks.setdefault(dev, anyio.Lock())
            return session

    def _preflight_conflicts(self, name: str, spec: TaskSpec) -> None:
        """Raise :class:`NIDaqResourceError` on obvious physical-channel overlap.

        Best-effort — only catches exact ``(device, physical_channel)``
        collisions across already-registered specs. NI is the authority;
        this exists so common operator mistakes (reusing ``Dev1/ai0`` on
        two tasks) fail fast with a clear message.
        """
        new_set = set(_channels_of(spec))
        if not new_set:
            return
        conflicts: dict[str, list[str]] = {}
        for other_name, other_spec in self._specs.items():
            other_set = set(_channels_of(other_spec))
            overlap = new_set & other_set
            if overlap:
                conflicts[other_name] = sorted(overlap)
        if conflicts:
            raise NIDaqResourceError(
                f"physical-channel conflict: task {name!r} overlaps with {sorted(conflicts)!r}",
                context=ErrorContext(
                    task_name=name,
                    operation="manager.add",
                    extra={"conflicts": conflicts},
                ),
            )

    async def remove(self, name: str) -> None:
        """Decrement refcount; tear down on the last :meth:`remove`.

        A no-op for unknown names — matches sibling parity.

        Raises:
            NIDaqError: Surfaced from session close (collected into a
                group when called from :meth:`close`).
        """
        async with self._global_lock:
            if name not in self._sessions:
                return
            self._refcounts[name] -= 1
            if self._refcounts[name] > 0:
                return
            session = self._sessions.pop(name)
            self._specs.pop(name, None)
            self._refcounts.pop(name, None)
            self._task_locks.pop(name, None)
            with contextlib.suppress(ValueError):
                self._order.remove(name)
        # Close outside the global lock so a slow NI close doesn't block
        # other manager ops on unrelated tasks.
        await session.close()

    def get(self, name: str) -> DaqSession:
        """Return the session registered under ``name``.

        Raises:
            KeyError: ``name`` is unknown.
        """
        return self._sessions[name]

    # -- Group operations ----------------------------------------------------

    async def start(
        self,
        names: Sequence[str] | None = None,
        *,
        error_policy: ErrorPolicy | None = None,
    ) -> Mapping[str, TaskResult[None]]:
        """Start one or more managed tasks. Defaults to all in add-order."""
        return await self._for_each(
            names,
            "start",
            self._call_start,
            error_policy=error_policy,
        )

    async def start_synchronized(
        self,
        master: str,
        slaves: Sequence[str],
        *,
        error_policy: ErrorPolicy | None = None,
    ) -> Mapping[str, TaskResult[None]]:
        """Arm ``slaves`` first, then start ``master``.

        Multi-task synchronisation requires strict ordering: each slave is
        configured against a shared sample clock or trigger and must reach
        the *armed-and-waiting* state before the master is started — once
        the master arms its clock or fires its trigger, the slaves react
        immediately. If a slave is started after the master, samples
        before its first edge are lost.

        Slaves are armed sequentially (not concurrently): NI's
        ``start_task`` returns once the task is armed, so issuing the
        starts in order guarantees every slave has reached the armed state
        before the master starts. This is intentionally simpler than the
        parallel fan-out used by :meth:`start`; the difference matters
        when one slave fails to arm — the master must not start at all.

        On failure during slave arming, every slave that had already
        armed is stopped (in reverse order) before the error is raised;
        the master is not started.

        Args:
            master: Manager-add name of the master task.
            slaves: Manager-add names of the slave tasks. Order is
                respected — slaves are armed left-to-right.
            error_policy: Optional override; defaults to the manager's
                policy.

        Returns:
            One :class:`TaskResult[None]` per task (``master`` plus every
            entry of ``slaves``), keyed by name.

        Raises:
            KeyError: ``master`` or any entry of ``slaves`` is unknown.
            BaseExceptionGroup: One or more tasks failed under
                :attr:`ErrorPolicy.RAISE`.
        """
        unknown = [n for n in (master, *slaves) if n not in self._sessions]
        if unknown:
            raise KeyError(f"unknown task name(s): {unknown!r}")
        if master in slaves:
            raise NIDaqTaskStateError(
                f"task {master!r} cannot be both master and slave",
                context=ErrorContext(task_name=master, operation="start_synchronized"),
            )

        policy = error_policy if error_policy is not None else self._error_policy
        results: dict[str, TaskResult[None]] = {}
        errors: list[BaseException] = []
        armed: list[str] = []

        for name in slaves:
            session = self._sessions[name]
            lock = self._task_locks[name]
            try:
                async with lock:
                    await session.start()
                results[name] = TaskResult(name=name, value=None, error=None)
                armed.append(name)
            except NIDaqError as exc:
                results[name] = TaskResult(name=name, value=None, error=exc)
                errors.append(exc)
                # Roll back: stop every slave that armed before this one,
                # in reverse order. Best-effort — collect rollback errors
                # but never raise from the rollback path.
                for prior in reversed(armed):
                    prior_session = self._sessions[prior]
                    prior_lock = self._task_locks[prior]
                    try:
                        async with prior_lock:
                            await prior_session.stop()
                    except NIDaqError as rollback_exc:
                        errors.append(rollback_exc)
                # Do not start the master.
                results[master] = TaskResult(
                    name=master,
                    value=None,
                    error=NIDaqTaskStateError(
                        f"master {master!r} not started: slave {name!r} failed to arm",
                        context=ErrorContext(task_name=master, operation="start_synchronized"),
                    ),
                )
                if policy is ErrorPolicy.RAISE:
                    raise BaseExceptionGroup(
                        "DaqManager.start_synchronized: slave arming failed",
                        errors,
                    ) from exc
                return results

        # All slaves armed — start the master.
        master_session = self._sessions[master]
        master_lock = self._task_locks[master]
        try:
            async with master_lock:
                await master_session.start()
            results[master] = TaskResult(name=master, value=None, error=None)
        except NIDaqError as exc:
            results[master] = TaskResult(name=master, value=None, error=exc)
            errors.append(exc)
            if policy is ErrorPolicy.RAISE:
                raise BaseExceptionGroup(
                    "DaqManager.start_synchronized: master start failed",
                    errors,
                ) from exc

        return results

    async def stop(
        self,
        names: Sequence[str] | None = None,
        *,
        error_policy: ErrorPolicy | None = None,
    ) -> Mapping[str, TaskResult[None]]:
        """Stop one or more managed tasks. Defaults to all in reverse-add."""
        targets = self._resolve_names(names)
        if names is None:
            targets = list(reversed(targets))
        return await self._for_each_targets(
            targets,
            "stop",
            self._call_stop,
            error_policy=error_policy,
        )

    async def poll(
        self,
        names: Sequence[str] | None = None,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
        error_policy: ErrorPolicy | None = None,
    ) -> Mapping[str, TaskResult[DaqReading]]:
        """Poll one or more tasks once each. Returns one :class:`DaqReading` per task."""

        async def _do(session: DaqSession) -> DaqReading:
            return await session.poll(timeout=timeout)

        return await self._for_each(
            names,
            "poll",
            _do,
            error_policy=error_policy,
        )

    async def read_block(
        self,
        samples_per_channel: int,
        names: Sequence[str] | None = None,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
        error_policy: ErrorPolicy | None = None,
    ) -> Mapping[str, TaskResult[DaqBlock]]:
        """Read one block per task in parallel."""

        async def _do(session: DaqSession) -> DaqBlock:
            return await session.read_block(samples_per_channel, timeout=timeout)

        return await self._for_each(
            names,
            "read_block",
            _do,
            error_policy=error_policy,
        )

    # -- Internal dispatchers -----------------------------------------------

    async def _call_start(self, session: DaqSession) -> None:
        await session.start()

    async def _call_stop(self, session: DaqSession) -> None:
        await session.stop()

    def _resolve_names(self, names: Sequence[str] | None) -> list[str]:
        if names is None:
            return list(self._order)
        unknown = [n for n in names if n not in self._sessions]
        if unknown:
            raise KeyError(f"unknown task name(s): {unknown!r}")
        return list(names)

    async def _for_each[U](
        self,
        names: Sequence[str] | None,
        op: str,
        fn: Callable[[DaqSession], Awaitable[U]],
        *,
        error_policy: ErrorPolicy | None,
    ) -> Mapping[str, TaskResult[U]]:
        targets = self._resolve_names(names)
        return await self._for_each_targets(targets, op, fn, error_policy=error_policy)

    async def _for_each_targets[U](
        self,
        targets: Sequence[str],
        op: str,
        fn: Callable[[DaqSession], Awaitable[U]],
        *,
        error_policy: ErrorPolicy | None,
    ) -> Mapping[str, TaskResult[U]]:
        policy = error_policy if error_policy is not None else self._error_policy
        results: dict[str, TaskResult[U]] = {}
        errors: list[BaseException] = []

        async def _run_one(name: str) -> None:
            session = self._sessions[name]
            lock = self._task_locks[name]
            try:
                async with lock:
                    value = await fn(session)
                results[name] = TaskResult(name=name, value=value, error=None)
            except NIDaqError as exc:
                results[name] = TaskResult(name=name, value=None, error=exc)
                if policy is ErrorPolicy.RAISE:
                    errors.append(exc)

        async with anyio.create_task_group() as tg:
            for name in targets:
                tg.start_soon(_run_one, name)

        if policy is ErrorPolicy.RAISE and errors:
            raise BaseExceptionGroup(f"DaqManager.{op} failed for one or more tasks", errors)
        return results

    # -- Close --------------------------------------------------------------

    async def close(self) -> None:
        """Tear down every managed session in LIFO order. Idempotent.

        Failures are collected into an :class:`ExceptionGroup`; one slow /
        broken close does not prevent others from running.
        """
        if self._closed:
            return
        self._closed = True
        # Snapshot under the global lock, then close outside it so unrelated
        # ops (e.g. a recorder still draining) are not blocked on a long NI
        # close call.
        async with self._global_lock:
            order = list(reversed(self._order))
            sessions = {name: self._sessions[name] for name in order if name in self._sessions}
            self._sessions.clear()
            self._specs.clear()
            self._refcounts.clear()
            self._order.clear()
            self._task_locks.clear()
        errors: list[BaseException] = []
        for name in order:
            session = sessions.get(name)
            if session is None:
                continue
            try:
                await session.close()
            except BaseException as exc:
                # Collected and re-grouped below — one slow / broken close
                # must not prevent the rest of the LIFO unwind from running.
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup("DaqManager.close: one or more sessions failed", errors)

    # -- Async context manager ----------------------------------------------

    async def __aenter__(self) -> DaqManager:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()
