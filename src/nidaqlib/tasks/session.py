"""Per-task lifecycle wrapper — :class:`DaqSession`.

A :class:`DaqSession` owns one underlying NI task plus the metadata needed
by the recorder layer: per-session lock (so concurrent reads don't trample
each other), the cumulative ``first_sample_index`` counter, and the
``task_started_at`` anchor that :class:`~nidaqlib.tasks.models.DaqBlock`
sample-time reconstruction depends on (design doc §8.7).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anyio
from anyio.to_thread import run_sync

from nidaqlib.errors import (
    ErrorContext,
    NIDaqConfigurationError,
    NIDaqTaskStateError,
    NIDaqValidationError,
)
from nidaqlib.tasks.models import DaqBlock, DaqReading
from nidaqlib.tasks.spec import AcquisitionMode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nidaqlib.backend.base import CallbackHandle, DaqBackend
    from nidaqlib.tasks.spec import TaskSpec


class DaqSession:
    """Owns one underlying NI task plus its lifecycle state.

    Construction does not touch the driver. Call :meth:`start` (or use
    :func:`open_task`) to create the task, add channels, and configure
    timing. ``read_block`` / ``poll`` are valid once started.
    """

    def __init__(
        self,
        spec: TaskSpec,
        backend: DaqBackend,
        *,
        timeout: float = 10.0,
    ) -> None:
        """Create a session for ``spec`` against ``backend``.

        The constructor only stores its arguments; it never touches the
        driver. That keeps ``__init__`` exception-free and avoids a
        partially-initialised task object on configuration errors.

        Args:
            spec: Declarative :class:`TaskSpec` to materialise.
            backend: Backend that proxies operations into NI (or the fake).
            timeout: Default per-operation timeout in seconds. Individual
                ``read_block`` / ``poll`` calls may override.
        """
        self._spec = spec
        self._backend = backend
        self._timeout = timeout
        self._task: Any = None
        self._lock = anyio.Lock()
        self._started = False
        self._closed = False
        self._task_started_at: datetime | None = None
        self._first_sample_index: int = 0
        self._block_index: int = 0
        # Bridge bookkeeping — populated only when streaming/block.py opts
        # into the every-N-samples callback path.
        self._callback_handle: CallbackHandle | None = None

    # -- Properties ----------------------------------------------------------

    @property
    def spec(self) -> TaskSpec:
        """The :class:`TaskSpec` this session was constructed from."""
        return self._spec

    @property
    def is_started(self) -> bool:
        """``True`` between :meth:`start` and :meth:`stop`."""
        return self._started

    @property
    def is_closed(self) -> bool:
        """``True`` once :meth:`close` has run (idempotent)."""
        return self._closed

    @property
    def raw_task(self) -> Any:
        """The underlying backend task handle.

        For :class:`~nidaqlib.backend.nidaqmx_backend.NidaqmxBackend` this is
        an ``nidaqmx.Task``; for the fake backend it is an opaque
        ``_FakeTask``. Use this for advanced NI features that aren't exposed
        via the wrapper — the escape hatch from design doc §7.4.

        Raises:
            NIDaqTaskStateError: The session has not been started yet (no
                task handle has been created).
        """
        if self._task is None:
            raise NIDaqTaskStateError(
                "raw_task is unavailable until the session is started",
                context=ErrorContext(task_name=self._spec.name, operation="raw_task"),
            )
        return self._task

    @property
    def task_started_at(self) -> datetime | None:
        """Wall-clock anchor for sample-time reconstruction.

        Returns ``None`` until :meth:`start` has succeeded. Once set, this
        value is the truth that :class:`DaqBlock.task_started_at` carries —
        it is captured exactly once per session, immediately before
        ``backend.start_task``, so that the first sample's wall-clock can be
        reconstructed deterministically from
        ``task_started_at + first_sample_index / rate_hz`` (design doc §8.7).
        """
        return self._task_started_at

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Create the underlying task, configure it, and start sampling.

        On failure, the partial task is torn down so the session does not
        leak NI resources.

        Raises:
            NIDaqTaskStateError: Already started or already closed.
        """
        if self._closed:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is closed",
                context=ErrorContext(task_name=self._spec.name, operation="start"),
            )
        if self._started:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is already started",
                context=ErrorContext(task_name=self._spec.name, operation="start"),
            )
        async with self._lock:
            await run_sync(self._configure_sync)
            # Capture the wall-clock anchor as close to the start as possible
            # — `start_task` returns once NI has armed the clock, so the
            # first sample's wall-clock is approximately this timestamp + a
            # bounded device latency.
            anchor = datetime.now(UTC)
            try:
                await run_sync(self._backend.start_task, self._task)
            except BaseException:
                await run_sync(self._backend.close_task, self._task)
                self._task = None
                raise
            self._task_started_at = anchor
            self._started = True

    def _configure_sync(self) -> None:
        """Synchronous portion of :meth:`start`. Runs on a worker thread."""
        task = self._backend.create_task(self._spec.name)
        try:
            for channel in self._spec.channels:
                self._backend.add_channel(task, channel)
            # NI ordering: configure_logging requires channels but must be
            # set before configure_timing — see design doc §14.6 and the NI
            # ``configure_logging`` reference. Triggers go *after* timing —
            # NI rejects ``cfg_dig_edge_ref_trig`` if the sample clock is
            # not yet configured.
            if self._spec.logging is not None:
                self._backend.configure_logging(task, self._spec.logging)
            if self._spec.timing is not None:
                self._backend.configure_timing(task, self._spec.timing)
            if self._spec.trigger is not None:
                self._backend.configure_trigger(task, self._spec.trigger)
        except BaseException:
            self._backend.close_task(task)
            raise
        self._task = task

    async def stop(self) -> None:
        """Stop the underlying task. Idempotent for not-yet-started sessions.

        Does NOT close the task. Use :meth:`close` to release NI resources.
        """
        if not self._started or self._closed or self._task is None:
            return
        async with self._lock:
            await run_sync(self._backend.stop_task, self._task)
            self._started = False

    async def close(self) -> None:
        """Stop (if needed) and close the underlying task. Idempotent.

        ``__aexit__`` always calls this; explicit call is rare. Sessions that
        have opted into the every-N-samples callback bridge MUST instead use
        the recorder context manager — the bridge has its own ordered
        shutdown protocol (design doc §11.3.2) that this method does not
        implement.
        """
        if self._closed:
            return
        self._closed = True
        if self._task is None:
            return
        async with self._lock:
            if self._started:
                await run_sync(self._backend.stop_task, self._task)
                self._started = False
            await run_sync(self._backend.close_task, self._task)
            self._task = None

    # -- Reads ---------------------------------------------------------------

    async def read_block(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
    ) -> DaqBlock:
        """Read one rectangular :class:`DaqBlock`.

        Wraps the backend read in an ``run_sync`` so the
        event loop stays responsive during the blocking NI call. Increments
        the per-session ``first_sample_index`` cursor.

        Args:
            samples_per_channel: Samples per channel for this block.
            timeout: Optional per-call timeout in seconds; falls back to the
                session-wide default.

        Raises:
            NIDaqTaskStateError: The session is not started or is closed.
            NIDaqReadError / NIDaqTimeoutError: Surfaced from the backend.
        """
        self._require_started("read_block")
        eff_timeout = timeout if timeout is not None else self._timeout
        async with self._lock:
            read_started_at = datetime.now(UTC)
            monotonic_ns = time.monotonic_ns()
            data = await run_sync(
                self._backend.read_block,
                self._task,
                samples_per_channel,
                eff_timeout,
            )
            read_finished_at = datetime.now(UTC)
            block = self._build_block(
                data=data,
                samples_per_channel=samples_per_channel,
                read_started_at=read_started_at,
                read_finished_at=read_finished_at,
                monotonic_ns=monotonic_ns,
            )
        return block

    async def acquire(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
    ) -> DaqBlock:
        """Run one finite acquisition and return its :class:`DaqBlock`.

        Convenience wrapper for the §12.3 finite-mode pattern: configure
        finite, start, read, stop. Requires a session whose
        :class:`Timing.mode` is :attr:`AcquisitionMode.FINITE`. After the
        read completes, the underlying NI task is stopped — call
        :meth:`start` again before another acquisition.

        Args:
            samples_per_channel: Number of samples per channel to read.
            timeout: Optional per-call timeout in seconds. Falls back to
                the session-wide default.

        Raises:
            NIDaqTaskStateError: The session is not started, is closed, or
                its timing mode is not :attr:`AcquisitionMode.FINITE`.
            NIDaqReadError / NIDaqTimeoutError: Surfaced from the backend.
        """
        self._require_started("acquire")
        timing = self._spec.timing
        if timing is None or timing.mode is not AcquisitionMode.FINITE:
            raise NIDaqTaskStateError(
                f"acquire() requires Timing.mode=FINITE; got {timing.mode if timing else None}",
                context=ErrorContext(task_name=self._spec.name, operation="acquire"),
            )
        block = await self.read_block(samples_per_channel, timeout=timeout)
        await self.stop()
        return block

    async def poll(
        self,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
    ) -> DaqReading:
        """One-shot scalar read across all channels.

        Valid only for sessions that are not actively buffering a sample
        clock (``Timing.mode == ON_DEMAND`` or no ``Timing`` at all). For the
        live-scalar use case during a high-rate acquisition, use
        :func:`record` and read the most recent block's last column.

        Raises:
            NIDaqTaskStateError: The session is buffering a sample clock
                (continuous or finite mode and started).
        """
        self._require_started("poll")
        timing = self._spec.timing
        if timing is not None and timing.mode in (
            AcquisitionMode.CONTINUOUS,
            AcquisitionMode.FINITE,
        ):
            raise NIDaqTaskStateError(
                f"poll() is invalid for {timing.mode.value} tasks; use record() and "
                "inspect the most recent DaqBlock instead",
                context=ErrorContext(task_name=self._spec.name, operation="poll"),
            )
        eff_timeout = timeout if timeout is not None else self._timeout
        async with self._lock:
            requested_at = datetime.now(UTC)
            monotonic_ns_start = time.monotonic_ns()
            data = await run_sync(
                self._backend.read_block,
                self._task,
                1,
                eff_timeout,
            )
            received_at = datetime.now(UTC)
            monotonic_ns_end = time.monotonic_ns()
        midpoint_at = requested_at + (received_at - requested_at) / 2
        midpoint_monotonic = (monotonic_ns_start + monotonic_ns_end) // 2
        # data shape is (n_channels, 1) — squeeze to per-channel scalars.
        names = self._channel_names()
        units = self._channel_units()
        values: dict[str, float | int | bool] = {
            name: float(data[i, 0]) for i, name in enumerate(names)
        }
        return DaqReading(
            device=self._spec.name,
            task=self._spec.name,
            values=values,
            units=units,
            requested_at=requested_at,
            received_at=received_at,
            midpoint_at=midpoint_at,
            monotonic_ns=midpoint_monotonic,
            elapsed_s=(received_at - requested_at).total_seconds(),
            metadata=dict(self._spec.metadata),
            error=None,
        )

    # -- Writes --------------------------------------------------------------

    async def write(
        self,
        values: Mapping[str, float | bool],
        *,
        confirm: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
    ) -> None:
        """Write one sample-per-channel to the task's output channels.

        Safety gate (design doc §17):

        - Keys of ``values`` must match the display names of the task's
          output channels (AO and/or DO). Unknown or missing keys raise
          :class:`NIDaqValidationError` before any I/O.
        - For analog-output channels with ``safe_min`` / ``safe_max`` set,
          values outside the resolved clamp window raise
          :class:`NIDaqValidationError`. **Never silently clamped.**
        - If any target channel has ``requires_confirm=True`` and
          ``confirm`` is ``False``, the call raises
          :class:`NIDaqValidationError`.

        Args:
            values: One value per output channel keyed by display name.
            confirm: Operator confirmation. Required (must be ``True``)
                whenever any target channel sets ``requires_confirm``.
            timeout: Per-call timeout in seconds. Falls back to the
                session-wide default.

        Raises:
            NIDaqTaskStateError: The session is not started or is closed.
            NIDaqValidationError: Safety-gate or shape rejection (see above).
            NIDaqWriteError / NIDaqTimeoutError: Surfaced from the backend.
        """
        # Late import — keeps the channel modules out of the session-import
        # graph for sessions that never write.
        from nidaqlib.channels.analog_output import AnalogOutputVoltage  # noqa: PLC0415
        from nidaqlib.channels.digital_output import DigitalOutput  # noqa: PLC0415

        self._require_started("write")

        output_channels = [
            ch for ch in self._spec.channels if isinstance(ch, (AnalogOutputVoltage, DigitalOutput))
        ]
        if not output_channels:
            raise NIDaqValidationError(
                f"task {self._spec.name!r} has no output channels to write",
                context=ErrorContext(task_name=self._spec.name, operation="write"),
            )

        target_names = {ch.display_name for ch in output_channels}
        provided_names = set(values.keys())
        unknown = provided_names - target_names
        missing = target_names - provided_names
        if unknown or missing:
            raise NIDaqValidationError(
                f"write keys do not match task outputs (unknown={sorted(unknown)!r}, "
                f"missing={sorted(missing)!r})",
                context=ErrorContext(task_name=self._spec.name, operation="write"),
            )

        needs_confirm = any(getattr(ch, "requires_confirm", False) for ch in output_channels)
        if needs_confirm and not confirm:
            raise NIDaqValidationError(
                f"task {self._spec.name!r}: write requires confirm=True (one or more "
                "channels are marked requires_confirm)",
                context=ErrorContext(task_name=self._spec.name, operation="write"),
            )

        for ch in output_channels:
            value = values[ch.display_name]
            if isinstance(ch, AnalogOutputVoltage):
                lo = ch.effective_safe_min
                hi = ch.effective_safe_max
                fvalue = float(value)
                if fvalue < lo or fvalue > hi:
                    raise NIDaqValidationError(
                        f"value {fvalue!r} for AO channel {ch.display_name!r} is outside "
                        f"safe range [{lo}, {hi}]",
                        context=ErrorContext(
                            task_name=self._spec.name,
                            channel_name=ch.display_name,
                            physical_channel=ch.physical_channel,
                            operation="write",
                        ),
                    )

        eff_timeout = timeout if timeout is not None else self._timeout
        async with self._lock:
            await run_sync(
                self._backend.write,
                self._task,
                dict(values),
                eff_timeout,
            )

    # -- Bridge plumbing (called by streaming/block.py) ----------------------

    def _set_callback_handle(self, handle: CallbackHandle | None) -> None:
        """Record the every-N-samples callback handle owned by the recorder.

        Internal — :mod:`nidaqlib.streaming.block` calls this so that the
        session can refuse :meth:`close` (which would tear down the task in
        the wrong order) while a bridge is active.
        """
        self._callback_handle = handle

    @property
    def has_active_callback_bridge(self) -> bool:
        """``True`` while a §11.3.2 callback bridge is registered."""
        return self._callback_handle is not None

    # -- Helpers -------------------------------------------------------------

    def _require_started(self, op: str) -> None:
        if self._closed:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is closed",
                context=ErrorContext(task_name=self._spec.name, operation=op),
            )
        if not self._started or self._task is None:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is not started",
                context=ErrorContext(task_name=self._spec.name, operation=op),
            )

    def _channel_names(self) -> tuple[str, ...]:
        return tuple(ch.display_name for ch in self._spec.channels)

    def _channel_units(self) -> Mapping[str, str | None]:
        return {ch.display_name: ch.unit for ch in self._spec.channels}

    def _build_block(
        self,
        *,
        data: Any,
        samples_per_channel: int,
        read_started_at: datetime,
        read_finished_at: datetime,
        monotonic_ns: int,
    ) -> DaqBlock:
        if self._task_started_at is None:
            raise NIDaqConfigurationError(
                "task_started_at is unset; this is an internal lifecycle bug",
                context=ErrorContext(task_name=self._spec.name, operation="_build_block"),
            )
        timing = self._spec.timing
        rate_hz = timing.rate_hz if timing is not None else None
        dt_s = (1.0 / rate_hz) if rate_hz else None
        block = DaqBlock(
            device=self._spec.name,
            task=self._spec.name,
            channels=self._channel_names(),
            data=data,
            block_index=self._block_index,
            first_sample_index=self._first_sample_index,
            samples_per_channel=samples_per_channel,
            sample_rate_hz=rate_hz,
            dt_s=dt_s,
            task_started_at=self._task_started_at,
            t0=read_started_at,
            monotonic_ns=monotonic_ns,
            read_started_at=read_started_at,
            read_finished_at=read_finished_at,
            elapsed_s=(read_finished_at - read_started_at).total_seconds(),
            units=self._channel_units(),
            error=None,
        )
        self._block_index += 1
        self._first_sample_index += samples_per_channel
        return block

    # -- Async context manager ----------------------------------------------

    async def __aenter__(self) -> DaqSession:
        """Enter the async context — calls :meth:`start`."""
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit the async context — calls :meth:`close`."""
        await self.close()


__all__ = ["DaqSession"]
