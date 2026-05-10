"""Backend implementation that delegates to ``nidaqmx-python``.

This is the production backend. It is the only module in :mod:`nidaqlib`
that imports ``nidaqmx`` at the call-site of every operation; failures from
the NI driver are wrapped in :class:`~nidaqlib.errors.NIDaqError` subclasses
with the original ``DaqError`` preserved as ``__cause__`` (design doc §16.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from nidaqlib.errors import (
    ErrorContext,
    NIDaqBackendError,
    NIDaqConfigurationError,
    NIDaqDependencyError,
    NIDaqReadError,
    NIDaqTimeoutError,
    NIDaqWriteError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import numpy as np

    from nidaqlib.channels.base import ChannelSpec
    from nidaqlib.system.models import DeviceInfo
    from nidaqlib.tasks.spec import TdmsLogging, Timing
    from nidaqlib.tasks.triggers import TriggerSpec


_NI_TIMEOUT_ERROR_CODE: Final[int] = -200284
"""``DAQmxErrorSamplesNotYetAvailable`` from ``nidaqmx.error_codes.DAQmxErrors``.

Hard-coded so the wrapper's timeout-detection branch does not need to import
NI's enum module — the error code is part of NI's stable C ABI and has not
changed in years.
"""


def _import_nidaqmx() -> Any:
    """Import ``nidaqmx`` lazily, surfacing a typed error when absent.

    Raises:
        NIDaqDependencyError: ``nidaqmx`` is not installed.
    """
    try:
        import nidaqmx  # noqa: PLC0415  — lazy import is intentional
    except ImportError as exc:  # pragma: no cover - exercised only without nidaqmx
        raise NIDaqDependencyError(
            "nidaqmx-python is required for NidaqmxBackend; install nidaqlib's core dependency set"
        ) from exc
    return nidaqmx


class NidaqmxBackend:
    """Production backend wrapping ``nidaqmx-python``.

    Supported operations:

    - Task creation / destruction.
    - Analog, digital, and counter channel addition.
    - Sample-clock timing.
    - Start / stop / read / write.
    - Trigger configuration.
    - Every-N-samples buffer-event callbacks (the §11.3.2 bridge driver).

    Per-task state held here is limited to the strong reference the
    callback bridge needs: NI stores the registered callback as a raw C
    function pointer (see §11.3.2 GC seam), and ``nidaqmx.Task`` uses
    ``__slots__`` so we can't stash the wrapper on the task itself. The
    backend keeps it in ``self._callback_wrappers`` keyed by ``id(task)``
    until ``unregister_every_n_samples`` runs.
    """

    def __init__(self) -> None:
        self._callback_wrappers: dict[int, Callable[..., int]] = {}

    def create_task(self, name: str) -> Any:
        """Create an ``nidaqmx.Task`` with ``name``.

        Raises:
            NIDaqBackendError: NI rejected the task creation.
        """
        nidaqmx = _import_nidaqmx()
        try:
            return nidaqmx.Task(new_task_name=name)
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                f"failed to create task {name!r}",
                context=ErrorContext(
                    task_name=name,
                    operation="create_task",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def close_task(self, task: Any) -> None:
        """Close ``task``. Idempotent — already-closed tasks are silently OK."""
        nidaqmx = _import_nidaqmx()
        try:
            task.close()
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to close task",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="close_task",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def add_channel(self, task: Any, spec: ChannelSpec) -> None:
        """Dispatch on ``spec.kind`` and add the channel to ``task``.

        The dispatch table covers ``"ai_voltage"``, ``"thermocouple"``,
        ``"ao_voltage"``, ``"di"``, ``"do"``, ``"ci_frequency"``,
        ``"ci_period"``, ``"ci_edge_count"``, ``"co_pulse_frequency"``,
        ``"co_pulse_time"``, and ``"co_pulse_ticks"``.

        Raises:
            NIDaqBackendError: NI rejected the channel creation, or
                ``spec.kind`` is unsupported by this backend.
        """
        # Late import — design doc §10.3 keeps the channel-spec module free
        # of the production NI dependency.
        from nidaqlib.channels.analog_input import (  # noqa: PLC0415
            AnalogInputVoltage,
            ThermocoupleInput,
        )
        from nidaqlib.channels.analog_output import AnalogOutputVoltage  # noqa: PLC0415
        from nidaqlib.channels.counter_input import (  # noqa: PLC0415
            CounterEdgeCountInput,
            CounterFrequencyInput,
            CounterPeriodInput,
        )
        from nidaqlib.channels.counter_output import (  # noqa: PLC0415
            CounterPulseFrequency,
            CounterPulseTicks,
            CounterPulseTime,
        )
        from nidaqlib.channels.digital_input import DigitalInput  # noqa: PLC0415
        from nidaqlib.channels.digital_output import DigitalOutput  # noqa: PLC0415

        nidaqmx = _import_nidaqmx()
        try:
            if isinstance(spec, AnalogInputVoltage):
                self._add_ai_voltage(task, spec)
            elif isinstance(spec, ThermocoupleInput):
                self._add_thermocouple(task, spec)
            elif isinstance(spec, AnalogOutputVoltage):
                self._add_ao_voltage(task, spec)
            elif isinstance(spec, (DigitalInput, DigitalOutput)):
                self._add_digital(task, spec)
            elif isinstance(spec, CounterFrequencyInput):
                self._add_ci_frequency(task, spec)
            elif isinstance(spec, CounterPeriodInput):
                self._add_ci_period(task, spec)
            elif isinstance(spec, CounterEdgeCountInput):
                self._add_ci_edge_count(task, spec)
            elif isinstance(spec, CounterPulseFrequency):
                self._add_co_pulse_frequency(task, spec)
            elif isinstance(spec, CounterPulseTime):
                self._add_co_pulse_time(task, spec)
            elif isinstance(spec, CounterPulseTicks):
                self._add_co_pulse_ticks(task, spec)
            else:
                raise NIDaqBackendError(
                    f"NidaqmxBackend does not support channel kind {spec.kind!r}",
                    context=ErrorContext(
                        task_name=getattr(task, "name", None),
                        physical_channel=spec.physical_channel,
                        operation="add_channel",
                    ),
                )
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                f"failed to add channel {spec.physical_channel!r}",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    channel_name=spec.name,
                    physical_channel=spec.physical_channel,
                    operation="add_channel",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def _add_ai_voltage(self, task: Any, spec: Any) -> None:
        kwargs: dict[str, Any] = {
            "physical_channel": spec.physical_channel,
            "min_val": spec.min_val,
            "max_val": spec.max_val,
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        if spec.terminal_config is not None:
            kwargs["terminal_config"] = spec.terminal_config
        if spec.custom_scale_name is not None:
            kwargs["custom_scale_name"] = spec.custom_scale_name
        chan = task.ai_channels.add_ai_voltage_chan(**kwargs)
        self._apply_ai_channel_attrs(chan, spec)

    def _add_thermocouple(self, task: Any, spec: Any) -> None:
        tc_kwargs: dict[str, Any] = {
            "physical_channel": spec.physical_channel,
            "min_val": spec.min_val,
            "max_val": spec.max_val,
            "thermocouple_type": spec.thermocouple_type,
            "units": spec.units,
        }
        if spec.name is not None:
            tc_kwargs["name_to_assign_to_channel"] = spec.name
        if spec.cjc_source is not None:
            tc_kwargs["cjc_source"] = spec.cjc_source
        if spec.cjc_val is not None:
            tc_kwargs["cjc_val"] = spec.cjc_val
        chan = task.ai_channels.add_ai_thrmcpl_chan(**tc_kwargs)
        self._apply_ai_channel_attrs(chan, spec)

    @staticmethod
    def _apply_ai_channel_attrs(chan: Any, spec: Any) -> None:
        """Apply per-channel NI attributes that aren't kwargs to ``add_ai_*_chan``.

        NI exposes a handful of AI-channel knobs (ADC timing mode,
        auto-zero mode, ...) only as properties on the channel object the
        ``add_*`` call returns. This helper writes each one when the spec
        opts in. The custom-timing-mode integer is paired with
        ``ADCTimingMode.CUSTOM`` (``__post_init__`` enforces that). Hardware
        that does not support an attribute raises ``DaqError`` here, which
        the calling :meth:`add_channel` handler wraps as
        :class:`NIDaqBackendError`.
        """
        if spec.adc_timing_mode is not None:
            chan.ai_adc_timing_mode = spec.adc_timing_mode
            if spec.adc_custom_timing_mode is not None:
                chan.ai_adc_custom_timing_mode = spec.adc_custom_timing_mode
        if spec.auto_zero_mode is not None:
            chan.ai_auto_zero_mode = spec.auto_zero_mode

    def _add_ao_voltage(self, task: Any, spec: Any) -> None:
        ao_kwargs: dict[str, Any] = {
            "physical_channel": spec.physical_channel,
            "min_val": spec.min_val,
            "max_val": spec.max_val,
        }
        if spec.name is not None:
            ao_kwargs["name_to_assign_to_channel"] = spec.name
        if spec.custom_scale_name is not None:
            ao_kwargs["custom_scale_name"] = spec.custom_scale_name
        task.ao_channels.add_ao_voltage_chan(**ao_kwargs)

    def _add_digital(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import LineGrouping  # noqa: PLC0415

        from nidaqlib.channels.digital_input import DigitalInput  # noqa: PLC0415

        grouping = (
            LineGrouping.CHAN_PER_LINE
            if spec.line_grouping_per_line
            else LineGrouping.CHAN_FOR_ALL_LINES
        )
        d_kwargs: dict[str, Any] = {
            "lines": spec.physical_channel,
            "line_grouping": grouping,
        }
        if spec.name is not None:
            d_kwargs["name_to_assign_to_lines"] = spec.name
        if isinstance(spec, DigitalInput):
            task.di_channels.add_di_chan(**d_kwargs)
        else:
            task.do_channels.add_do_chan(**d_kwargs)

    def _add_ci_frequency(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import Edge as NIEdge  # noqa: PLC0415

        from nidaqlib.tasks.spec import Edge  # noqa: PLC0415

        edge_map = {Edge.RISING: NIEdge.RISING, Edge.FALLING: NIEdge.FALLING}
        kwargs: dict[str, Any] = {
            "counter": spec.physical_channel,
            "min_val": spec.min_val,
            "max_val": spec.max_val,
            "edge": edge_map[spec.edge],
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        task.ci_channels.add_ci_freq_chan(**kwargs)

    def _add_ci_period(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import Edge as NIEdge  # noqa: PLC0415

        from nidaqlib.tasks.spec import Edge  # noqa: PLC0415

        edge_map = {Edge.RISING: NIEdge.RISING, Edge.FALLING: NIEdge.FALLING}
        kwargs: dict[str, Any] = {
            "counter": spec.physical_channel,
            "min_val": spec.min_val,
            "max_val": spec.max_val,
            "starting_edge": edge_map[spec.edge],
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        task.ci_channels.add_ci_period_chan(**kwargs)

    def _add_ci_edge_count(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import CountDirection  # noqa: PLC0415
        from nidaqmx.constants import Edge as NIEdge  # noqa: PLC0415

        from nidaqlib.tasks.spec import Edge  # noqa: PLC0415

        edge_map = {Edge.RISING: NIEdge.RISING, Edge.FALLING: NIEdge.FALLING}
        kwargs: dict[str, Any] = {
            "counter": spec.physical_channel,
            "edge": edge_map[spec.edge],
            "initial_count": spec.initial_count,
            "count_direction": (
                CountDirection.COUNT_UP if spec.count_up else CountDirection.COUNT_DOWN
            ),
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        task.ci_channels.add_ci_count_edges_chan(**kwargs)

    def _add_co_pulse_frequency(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import Level  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "counter": spec.physical_channel,
            "freq": spec.frequency,
            "duty_cycle": spec.duty_cycle,
            "initial_delay": spec.initial_delay,
            "idle_state": Level.HIGH if spec.idle_high else Level.LOW,
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        task.co_channels.add_co_pulse_chan_freq(**kwargs)

    def _add_co_pulse_time(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import Level  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "counter": spec.physical_channel,
            "high_time": spec.high_time,
            "low_time": spec.low_time,
            "initial_delay": spec.initial_delay,
            "idle_state": Level.HIGH if spec.idle_high else Level.LOW,
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        task.co_channels.add_co_pulse_chan_time(**kwargs)

    def _add_co_pulse_ticks(self, task: Any, spec: Any) -> None:
        from nidaqmx.constants import Level  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "counter": spec.physical_channel,
            "source_terminal": spec.source_terminal,
            "high_ticks": spec.high_ticks,
            "low_ticks": spec.low_ticks,
            "initial_delay": spec.initial_delay,
            "idle_state": Level.HIGH if spec.idle_high else Level.LOW,
        }
        if spec.name is not None:
            kwargs["name_to_assign_to_channel"] = spec.name
        task.co_channels.add_co_pulse_chan_ticks(**kwargs)

    def configure_timing(self, task: Any, timing: Timing) -> None:
        """Apply :class:`Timing` to ``task`` via ``cfg_samp_clk_timing``.

        Raises:
            NIDaqBackendError: NI rejected the timing configuration.
        """
        from nidaqlib.tasks.spec import AcquisitionMode, Edge  # noqa: PLC0415

        nidaqmx = _import_nidaqmx()
        from nidaqmx.constants import AcquisitionType  # noqa: PLC0415
        from nidaqmx.constants import Edge as NIEdge  # noqa: PLC0415

        if timing.mode is AcquisitionMode.ON_DEMAND:
            return

        mode_map = {
            AcquisitionMode.FINITE: AcquisitionType.FINITE,
            AcquisitionMode.CONTINUOUS: AcquisitionType.CONTINUOUS,
        }
        edge_map = {Edge.RISING: NIEdge.RISING, Edge.FALLING: NIEdge.FALLING}
        # ``samps_per_chan`` is required by NI for both finite and continuous
        # modes — for continuous it sizes the on-board buffer. Pass a sane
        # default when the user did not supply one.
        samps = (
            timing.samples_per_channel
            if timing.samples_per_channel is not None
            else max(1000, int(timing.rate_hz))
        )
        kwargs: dict[str, Any] = {
            "rate": timing.rate_hz,
            "active_edge": edge_map[timing.active_edge],
            "sample_mode": mode_map[timing.mode],
            "samps_per_chan": samps,
        }
        if timing.source is not None:
            kwargs["source"] = timing.source
        try:
            task.timing.cfg_samp_clk_timing(**kwargs)
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to configure timing",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="configure_timing",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def configure_logging(self, task: Any, logging: TdmsLogging) -> None:
        """Configure driver-side TDMS logging via ``task.in_stream``.

        Raises:
            NIDaqBackendError: NI rejected the configure-logging call.
        """
        nidaqmx = _import_nidaqmx()
        kwargs: dict[str, Any] = {
            "file_path": str(logging.path),
            "logging_mode": logging.mode,
            "operation": logging.operation,
        }
        if logging.group_name is not None:
            kwargs["group_name"] = logging.group_name
        try:
            task.in_stream.configure_logging(**kwargs)
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to configure TDMS logging",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="configure_logging",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def configure_trigger(self, task: Any, trigger: TriggerSpec) -> None:
        """Dispatch ``trigger`` onto the appropriate NI ``triggers.*`` API.

        Raises:
            NIDaqBackendError: NI rejected the trigger configuration, or
                the trigger ``kind`` is unsupported by this backend.
            NIDaqConfigurationError: The trigger spec is structurally
                invalid (e.g. zero pretrigger samples — already caught by
                ``__post_init__``, but defensive).
        """
        from nidaqlib.tasks.spec import Edge  # noqa: PLC0415
        from nidaqlib.tasks.triggers import (  # noqa: PLC0415
            AnalogEdgeStartTrigger,
            AnalogTriggerSlope,
            DigitalEdgeReferenceTrigger,
            DigitalEdgeStartTrigger,
        )

        nidaqmx = _import_nidaqmx()
        from nidaqmx.constants import Edge as NIEdge  # noqa: PLC0415
        from nidaqmx.constants import Slope as NISlope  # noqa: PLC0415

        edge_map = {Edge.RISING: NIEdge.RISING, Edge.FALLING: NIEdge.FALLING}
        slope_map = {
            AnalogTriggerSlope.RISING: NISlope.RISING,
            AnalogTriggerSlope.FALLING: NISlope.FALLING,
        }
        try:
            if isinstance(trigger, DigitalEdgeStartTrigger):
                task.triggers.start_trigger.cfg_dig_edge_start_trig(
                    trigger_source=trigger.source,
                    trigger_edge=edge_map[trigger.edge],
                )
            elif isinstance(trigger, AnalogEdgeStartTrigger):
                task.triggers.start_trigger.cfg_anlg_edge_start_trig(
                    trigger_source=trigger.source,
                    trigger_slope=slope_map[trigger.slope],
                    trigger_level=trigger.level,
                )
            elif isinstance(trigger, DigitalEdgeReferenceTrigger):
                task.triggers.reference_trigger.cfg_dig_edge_ref_trig(
                    trigger_source=trigger.source,
                    pretrigger_samples=trigger.pretrigger_samples,
                    trigger_edge=edge_map[trigger.edge],
                )
            else:
                raise NIDaqBackendError(
                    f"NidaqmxBackend does not support trigger kind {trigger.kind!r}",
                    context=ErrorContext(
                        task_name=getattr(task, "name", None),
                        operation="configure_trigger",
                    ),
                )
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to configure trigger",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="configure_trigger",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def start_task(self, task: Any) -> None:
        """Start ``task``."""
        nidaqmx = _import_nidaqmx()
        try:
            task.start()
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to start task",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="start_task",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def stop_task(self, task: Any) -> None:
        """Stop ``task``."""
        nidaqmx = _import_nidaqmx()
        try:
            task.stop()
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to stop task",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="stop_task",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc

    def read_block(
        self,
        task: Any,
        samples_per_channel: int,
        timeout: float,
    ) -> np.ndarray:
        """Block-read ``samples_per_channel`` samples per channel.

        Uses :class:`AnalogMultiChannelReader` so the result is
        ``np.ndarray`` of shape ``(n_channels, samples_per_channel)`` rather
        than ``Task.read``'s list-of-lists. Allocates a fresh buffer per call
        because clarity is worth a few microseconds of allocator pressure here.

        Raises:
            NIDaqTimeoutError: ``timeout`` elapsed before samples were ready.
            NIDaqReadError: NI returned any other read failure.
        """
        import numpy as np  # noqa: PLC0415
        from nidaqmx.stream_readers import AnalogMultiChannelReader  # noqa: PLC0415

        nidaqmx = _import_nidaqmx()

        n_channels = int(task.number_of_channels)
        buf = np.empty((n_channels, samples_per_channel), dtype=np.float64)
        reader = AnalogMultiChannelReader(task.in_stream)
        read_many_sample = cast(
            "Callable[..., object]",
            reader.read_many_sample,  # pyright: ignore[reportUnknownMemberType]
        )
        try:
            read_many_sample(
                buf,
                number_of_samples_per_channel=samples_per_channel,
                timeout=timeout,
            )
        except nidaqmx.errors.DaqError as exc:
            ctx = ErrorContext(
                task_name=getattr(task, "name", None),
                operation="read_block",
                ni_error_code=getattr(exc, "error_code", None),
            )
            # NI's timeout error code is DAQmxErrorSamplesNotYetAvailable;
            # generic read failures land under NIDaqReadError.
            if getattr(exc, "error_code", None) == _NI_TIMEOUT_ERROR_CODE:
                raise NIDaqTimeoutError(
                    f"read_block timed out after {timeout}s",
                    context=ctx,
                ) from exc
            raise NIDaqReadError(
                "failed to read DAQ block",
                context=ctx,
            ) from exc
        return buf

    def write(
        self,
        task: Any,
        values: Mapping[str, float | bool],
        timeout: float,
    ) -> None:
        """Dispatch one-sample-per-channel write across AO / DO channels.

        Inspects the underlying NI task's channel collections — AO writes go
        through ``AnalogMultiChannelWriter``; DO writes through
        ``DigitalMultiChannelWriter``. Mixing AO and DO on a single task is
        rejected as :class:`NIDaqConfigurationError`. Per-channel ordering
        follows ``task.channel_names`` so the caller's mapping does not need
        to match NI's internal order.

        Raises:
            NIDaqConfigurationError: Task mixes AO and DO, or the keys of
                ``values`` don't cover the task's output channels.
            NIDaqWriteError / NIDaqTimeoutError: Surfaced from the backend.
        """
        import numpy as np  # noqa: PLC0415

        nidaqmx = _import_nidaqmx()

        ao_count = int(getattr(task, "number_of_ao_channels", 0) or 0)
        do_count = 0
        try:
            do_count = len(list(task.do_channels))
        except Exception:  # pragma: no cover - defensive against odd NI shapes
            do_count = 0

        if ao_count > 0 and do_count > 0:
            raise NIDaqConfigurationError(
                "tasks mixing AO and DO are not supported by NidaqmxBackend.write",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="write",
                ),
            )

        channel_names: list[str] = list(task.channel_names)
        missing = [name for name in channel_names if name not in values]
        if missing:
            raise NIDaqConfigurationError(
                f"write missing values for channel(s): {missing!r}",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="write",
                ),
            )

        ctx = ErrorContext(task_name=getattr(task, "name", None), operation="write")
        try:
            if ao_count > 0:
                from nidaqmx.stream_writers import AnalogMultiChannelWriter  # noqa: PLC0415

                buf = np.asarray([float(values[name]) for name in channel_names], dtype=np.float64)
                writer = AnalogMultiChannelWriter(task.out_stream)
                write_one_sample = cast(
                    "Callable[..., object]",
                    writer.write_one_sample,  # pyright: ignore[reportUnknownMemberType]
                )
                write_one_sample(buf, timeout=timeout)
                return
            if do_count > 0:
                from nidaqmx.stream_writers import DigitalMultiChannelWriter  # noqa: PLC0415

                buf_b = np.asarray([bool(values[name]) for name in channel_names], dtype=bool)
                writer_d = DigitalMultiChannelWriter(task.out_stream)
                write_one_sample_d = cast(
                    "Callable[..., object]",
                    writer_d.write_one_sample_one_line,  # pyright: ignore[reportUnknownMemberType]
                )
                write_one_sample_d(buf_b, timeout=timeout)
                return
            raise NIDaqConfigurationError(
                "task has no writable channels (AO or DO)",
                context=ctx,
            )
        except nidaqmx.errors.DaqError as exc:
            ni_ctx = ErrorContext(
                task_name=getattr(task, "name", None),
                operation="write",
                ni_error_code=getattr(exc, "error_code", None),
            )
            if getattr(exc, "error_code", None) == _NI_TIMEOUT_ERROR_CODE:
                raise NIDaqTimeoutError(
                    f"write timed out after {timeout}s",
                    context=ni_ctx,
                ) from exc
            raise NIDaqWriteError(
                "failed to write DAQ values",
                context=ni_ctx,
            ) from exc

    def register_every_n_samples(
        self,
        task: Any,
        n: int,
        callback: Callable[[int], None],
    ) -> Any:
        """Register a buffer-event callback for ``task``.

        Wraps NI's four-argument C-style callback into the Protocol's
        single-argument ``Callable[[int], None]``. Returns ``task`` itself —
        NI tracks at most one such callback per task, so the unregister side
        reuses the same handle.

        The caller MUST keep a strong reference to ``callback`` for the
        lifetime of the registration. NI stores the wrapper as a raw C
        function pointer and Python GC will silently break the seam.

        Raises:
            NIDaqBackendError: NI rejected the registration.
        """
        nidaqmx = _import_nidaqmx()

        def _ni_cb(task_handle: object, event_type: int, n_samples: int, _data: object) -> int:
            """NI-shaped trampoline.

            Runs on a DAQmx driver thread. Anyio / asyncio APIs are unsafe
            here — the bridge layer (``streaming/block.py``) takes care of
            the thread-safe hand-off via ``queue.SimpleQueue``.
            """
            del task_handle, event_type, _data
            callback(n_samples)
            return 0

        try:
            task.register_every_n_samples_acquired_into_buffer_event(n, _ni_cb)
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to register every-N-samples callback",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="register_every_n_samples",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc
        # Strong-ref the wrapper for the lifetime of the registration. NI
        # stores ``_ni_cb`` as a raw C function pointer; if Python GC reaps
        # it, the next firing crashes the driver (see §11.3.2 GC seam).
        # ``nidaqmx.Task`` uses ``__slots__`` so we can't stash on the task
        # itself — keep it on the backend instance, keyed by id(task).
        self._callback_wrappers[id(task)] = _ni_cb
        return task

    def unregister_every_n_samples(self, task: Any, handle: Any) -> None:
        """Unregister the buffer-event callback on ``task``.

        Per NI's API, registering with ``None`` clears the callback.

        Raises:
            NIDaqBackendError: NI rejected the unregister call.
        """
        del handle  # NI's API is task-scoped; the handle is the task itself.
        nidaqmx = _import_nidaqmx()
        try:
            task.register_every_n_samples_acquired_into_buffer_event(0, None)
        except nidaqmx.errors.DaqError as exc:
            raise NIDaqBackendError(
                "failed to unregister every-N-samples callback",
                context=ErrorContext(
                    task_name=getattr(task, "name", None),
                    operation="unregister_every_n_samples",
                    ni_error_code=getattr(exc, "error_code", None),
                ),
            ) from exc
        # Drop the strong reference now that NI is no longer holding it.
        self._callback_wrappers.pop(id(task), None)

    def device_info(self, device: str) -> DeviceInfo | None:
        """Return product info for ``device`` via ``nidaqmx.system.Device``.

        Direct lookup — does not enumerate the whole system. Returns
        ``None`` if NI does not recognise the device alias. Used by the
        manager's preflight to detect module-level reservation (e.g. TC
        modules reserve the whole module per task).
        """
        from nidaqlib.system.models import DeviceInfo as _DeviceInfo  # noqa: PLC0415

        nidaqmx = _import_nidaqmx()
        try:
            dev = nidaqmx.system.Device(device)
            product_type = getattr(dev, "product_type", None)
        except nidaqmx.errors.DaqError:
            return None
        if product_type is None:
            return None
        # Only product_type matters for the reservation lookup; channel
        # inventories are a separate (more expensive) call we don't need
        # here. Leave them empty.
        return _DeviceInfo(
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


__all__ = ["NidaqmxBackend"]
