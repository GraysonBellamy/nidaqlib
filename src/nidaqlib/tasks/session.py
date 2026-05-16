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
    NIDaqConfirmationRequiredError,
    NIDaqTaskStateError,
    NIDaqValidationError,
)
from nidaqlib.tasks.models import DaqBlock, DaqReading, NIDaqSnapshot, TaskState
from nidaqlib.tasks.spec import AcquisitionMode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nidaqlib.backend.base import CallbackHandle, DaqBackend
    from nidaqlib.system.models import DeviceInfo
    from nidaqlib.tasks.spec import TaskSpec


class DaqSession:
    """Owns one underlying NI task plus its lifecycle state.

    Construction does not touch the driver. Call :meth:`start` (or use
    :func:`open_device`) to create the task, add channels, and configure
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
        self._configured = False
        self._started = False
        self._closed = False
        self._task_started_at: datetime | None = None
        self._first_sample_index: int = 0
        self._block_index: int = 0
        # Counter incremented every time the recorder swallows a
        # NIDaqTransientError under ErrorPolicy.RETURN. Reset on task
        # rebuild (a fresh configure() call).
        self._recoverable_error_count: int = 0
        # Cached identity, populated by configure_sync (one backend call per
        # task build). Keeps :meth:`snapshot` I/O-free.
        self._device_info: DeviceInfo | None = None
        # Last enriched error context observed; updated when the session
        # surfaces a NIDaqError from its own methods. Snapshots embed this.
        self._last_error_context: ErrorContext | None = None
        # Bridge bookkeeping — populated only when streaming/block.py opts
        # into the every-N-samples callback path.
        self._callback_handle: CallbackHandle | None = None

    # -- Properties ----------------------------------------------------------

    @property
    def spec(self) -> TaskSpec:
        """The :class:`TaskSpec` this session was constructed from."""
        return self._spec

    @property
    def is_configured(self) -> bool:
        """``True`` after :meth:`configure` succeeds and before :meth:`close`.

        A configured session has a backing NI task with channels, timing,
        logging, and triggers applied — but ``task.start()`` has not yet
        been called. Buffer-event callback registration (§11.3.2) is only
        valid in this window.
        """
        return self._configured and not self._closed

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

        The handle is available once :meth:`configure` has succeeded — that
        is, in either the configured-not-started or started state. The
        callback bridge (§11.3.2) needs the handle pre-start to register
        the buffer event.

        Raises:
            NIDaqTaskStateError: The session has not been configured yet.
        """
        if self._task is None:
            raise NIDaqTaskStateError(
                "raw_task is unavailable until the session is configured",
                context=ErrorContext(task_name=self._spec.name, command_name="raw_task"),
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

    @property
    def recoverable_error_count(self) -> int:
        """Count of :class:`NIDaqTransientError` events swallowed under RETURN policy.

        Reset to ``0`` on every :meth:`configure` (i.e. fresh task build).
        """
        return self._recoverable_error_count

    @property
    def device_info(self) -> DeviceInfo | None:
        """Cached identity for the device backing this task; ``None`` before configure."""
        return self._device_info

    def task_state(self) -> TaskState:
        """Return the coarse :class:`TaskState` projection of this session.

        I/O-free — derived from internal lifecycle flags.
        """
        if self._closed:
            return TaskState.CLOSED
        if self._started:
            return TaskState.RUNNING
        if self._configured:
            # `configured-but-not-started` covers both "never started" and
            # "started then stopped". We distinguish by whether the start
            # anchor was ever set.
            return TaskState.STOPPED if self._task_started_at is not None else TaskState.CONFIGURED
        return TaskState.CREATED

    async def snapshot(self) -> NIDaqSnapshot:
        """Return an :class:`NIDaqSnapshot` of this session's current state.

        No I/O — built from the cached :class:`DeviceInfo` (one backend call
        at configure time) and the session's own lifecycle flags. Safe to
        call from any thread / event loop / callback context.
        """
        info = self._device_info
        timing = self._spec.timing
        return NIDaqSnapshot(
            name=self._spec.name,
            model=info.product_type if info is not None else None,
            firmware=None,
            serial=info.serial_number if info is not None else None,
            connected=self._configured and not self._closed,
            last_error=self._last_error_context,
            recoverable_error_count=self._recoverable_error_count,
            captured_at=datetime.now(UTC),
            task_name=self._spec.name,
            task_state=self.task_state(),
            channel_count=len(self._spec.channels),
            timing_mode=timing.mode.value if timing is not None else None,
            rate_hz=timing.rate_hz if timing is not None else None,
            physical_channels=tuple(ch.physical_channel for ch in self._spec.channels),
            product_type=info.product_type if info is not None else None,
            chassis=None,
            physical_module=info.name if info is not None else None,
        )

    # -- Lifecycle -----------------------------------------------------------

    async def configure(self) -> None:
        """Create the underlying task and apply channels / timing / logging / trigger.

        After this method, ``raw_task`` is available and any pre-start hooks
        (notably the §11.3.2 buffer-event callback registration) may run.
        ``task.start()`` is **not** called — use :meth:`start` for that.

        On failure, the partial task is torn down so the session does not
        leak NI resources.

        Raises:
            NIDaqTaskStateError: Already configured, started, or closed.
        """
        if self._closed:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is closed",
                context=ErrorContext(task_name=self._spec.name, command_name="configure"),
            )
        if self._configured:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is already configured",
                context=ErrorContext(task_name=self._spec.name, command_name="configure"),
            )
        async with self._lock:
            await run_sync(self._configure_sync)
            self._configured = True

    async def start(self, *, confirm: bool = False) -> None:
        """Start the configured task.

        :meth:`configure` must have run first. This method calls NI's
        ``task.start()`` and records the wall-clock anchor used for §8.7
        sample-time reconstruction. Calling :meth:`start` again after
        :meth:`stop` reuses the configured task and resets the
        block/sample counters for a new run.

        ``confirm=True`` is required for task kinds whose ``start`` call
        can actuate hardware immediately (currently counter-output pulse
        trains).

        Raises:
            NIDaqTaskStateError: Not configured, already started, or closed.
            NIDaqValidationError: Starting would actuate hardware without
                explicit confirmation.
        """
        if self._closed:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is closed",
                context=ErrorContext(task_name=self._spec.name, command_name="start"),
            )
        if not self._configured:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} must be configured before start",
                context=ErrorContext(task_name=self._spec.name, command_name="start"),
            )
        if self._started:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is already started",
                context=ErrorContext(task_name=self._spec.name, command_name="start"),
            )
        self._validate_start_safety(confirm=confirm)
        async with self._lock:
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
                self._configured = False
                raise
            self._task_started_at = anchor
            self._first_sample_index = 0
            self._block_index = 0
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
            if (
                self._spec.timing is not None
                and self._spec.timing.mode is not AcquisitionMode.ON_DEMAND
            ):
                self._backend.configure_timing(task, self._spec.timing)
            if self._spec.trigger is not None:
                self._backend.configure_trigger(task, self._spec.trigger)
        except BaseException:
            self._backend.close_task(task)
            raise
        self._task = task
        # Cache identity once per task build — keeps snapshot() I/O-free.
        first_channel = self._spec.channels[0].physical_channel if self._spec.channels else ""
        device_name = first_channel.split("/", 1)[0] if "/" in first_channel else first_channel
        self._device_info = self._backend.device_info(device_name) if device_name else None
        # Reset transient-error counter on a fresh task build.
        self._recoverable_error_count = 0

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
        if self._callback_handle is not None:
            raise NIDaqTaskStateError(
                "cannot close a session while an every-N-samples callback bridge is active; "
                "exit the record(..., use_callback_bridge=True) context first",
                context=ErrorContext(task_name=self._spec.name, command_name="close"),
            )
        self._closed = True
        if self._task is None:
            return
        async with self._lock:
            if self._started:
                await run_sync(self._backend.stop_task, self._task)
                self._started = False
            await run_sync(self._backend.close_task, self._task)
            self._task = None
            self._configured = False

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
        self._require_analog_input_task("read_block")
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
                context=ErrorContext(task_name=self._spec.name, command_name="acquire"),
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
        self._require_analog_input_task("poll")
        timing = self._spec.timing
        if timing is not None and timing.mode in (
            AcquisitionMode.CONTINUOUS,
            AcquisitionMode.FINITE,
        ):
            raise NIDaqTaskStateError(
                f"poll() is invalid for {timing.mode.value} tasks; use record() and "
                "inspect the most recent DaqBlock instead",
                context=ErrorContext(task_name=self._spec.name, command_name="poll"),
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
        t_utc = requested_at + (received_at - requested_at) / 2
        t_mono_ns = (monotonic_ns_start + monotonic_ns_end) // 2
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
            t_mono_ns=t_mono_ns,
            t_utc=t_utc,
            t_midpoint_mono_ns=None,
            requested_at=requested_at,
            received_at=received_at,
            latency_s=(received_at - requested_at).total_seconds(),
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
        from nidaqlib.channels.counter_output import (  # noqa: PLC0415
            CounterPulseFrequency,
            CounterPulseTicks,
            CounterPulseTime,
        )
        from nidaqlib.channels.digital_output import DigitalOutput  # noqa: PLC0415

        self._require_started("write")

        output_channels = [
            ch for ch in self._spec.channels if isinstance(ch, (AnalogOutputVoltage, DigitalOutput))
        ]
        if not output_channels:
            if any(
                isinstance(ch, (CounterPulseFrequency, CounterPulseTime, CounterPulseTicks))
                for ch in self._spec.channels
            ):
                raise NIDaqValidationError(
                    "counter-output pulse trains are controlled by start()/stop(), not write(); "
                    "start them with confirm=True",
                    context=ErrorContext(task_name=self._spec.name, command_name="write"),
                )
            raise NIDaqValidationError(
                f"task {self._spec.name!r} has no output channels to write",
                context=ErrorContext(task_name=self._spec.name, command_name="write"),
            )
        has_ao = any(isinstance(ch, AnalogOutputVoltage) for ch in output_channels)
        has_do = any(isinstance(ch, DigitalOutput) for ch in output_channels)
        if has_ao and has_do:
            raise NIDaqValidationError(
                "write() does not support mixing analog-output and digital-output "
                "channels in one task",
                context=ErrorContext(task_name=self._spec.name, command_name="write"),
            )

        target_names = {ch.display_name for ch in output_channels}
        provided_names = set(values.keys())
        unknown = provided_names - target_names
        missing = target_names - provided_names
        if unknown or missing:
            raise NIDaqValidationError(
                f"write keys do not match task outputs (unknown={sorted(unknown)!r}, "
                f"missing={sorted(missing)!r})",
                context=ErrorContext(task_name=self._spec.name, command_name="write"),
            )

        needs_confirm = any(getattr(ch, "requires_confirm", False) for ch in output_channels)
        if needs_confirm and not confirm:
            raise NIDaqConfirmationRequiredError(
                f"task {self._spec.name!r}: write requires confirm=True (one or more "
                "channels are marked requires_confirm)",
                context=ErrorContext(task_name=self._spec.name, command_name="write"),
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
                            command_name="write",
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
                context=ErrorContext(task_name=self._spec.name, command_name=op),
            )
        if not self._started or self._task is None:
            raise NIDaqTaskStateError(
                f"session for task {self._spec.name!r} is not started",
                context=ErrorContext(task_name=self._spec.name, command_name=op),
            )

    def _validate_start_safety(self, *, confirm: bool) -> None:
        """Require confirmation before starting task kinds that actuate immediately."""
        from nidaqlib.channels.counter_output import (  # noqa: PLC0415
            CounterPulseFrequency,
            CounterPulseTicks,
            CounterPulseTime,
        )

        actuating = [
            ch.display_name
            for ch in self._spec.channels
            if isinstance(ch, (CounterPulseFrequency, CounterPulseTime, CounterPulseTicks))
            and getattr(ch, "requires_confirm", False)
        ]
        if actuating and not confirm:
            raise NIDaqConfirmationRequiredError(
                f"starting task {self._spec.name!r} requires confirm=True; "
                f"counter-output channels would actuate immediately: {actuating!r}",
                context=ErrorContext(task_name=self._spec.name, command_name="start"),
            )

    def _require_analog_input_task(self, op: str) -> None:
        """Reject task shapes the current read path cannot represent correctly."""
        from nidaqlib.channels.analog_input import (  # noqa: PLC0415
            AnalogInputVoltage,
            ThermocoupleInput,
        )

        unsupported = [
            ch.display_name
            for ch in self._spec.channels
            if not isinstance(ch, (AnalogInputVoltage, ThermocoupleInput))
        ]
        if unsupported:
            raise NIDaqValidationError(
                f"{op} currently supports analog-input tasks only; unsupported channels: "
                f"{unsupported!r}",
                context=ErrorContext(task_name=self._spec.name, command_name=op),
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
                context=ErrorContext(task_name=self._spec.name, command_name="_build_block"),
            )
        timing = self._spec.timing
        rate_hz = (
            timing.rate_hz
            if timing is not None and timing.mode is not AcquisitionMode.ON_DEMAND
            else None
        )
        block_period_ns = int(1e9 / rate_hz) if rate_hz else None
        if block_period_ns is not None:
            midpoint_offset_ns = block_period_ns * (samples_per_channel - 1) // 2
            t_midpoint_mono_ns: int | None = monotonic_ns + midpoint_offset_ns
        else:
            t_midpoint_mono_ns = None
        block = DaqBlock(
            device=self._spec.name,
            task=self._spec.name,
            channels=self._channel_names(),
            data=data,
            block_index=self._block_index,
            first_sample_index=self._first_sample_index,
            samples_per_channel=samples_per_channel,
            block_period_ns=block_period_ns,
            t_mono_ns=monotonic_ns,
            t_utc=read_started_at,
            t_midpoint_mono_ns=t_midpoint_mono_ns,
            task_started_at=self._task_started_at,
            t0=read_started_at,
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
        """Enter the async context — no-op; :func:`open_device` already configured/started."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit the async context — calls :meth:`close`."""
        del exc_info
        await self.close()


__all__ = ["DaqSession"]
