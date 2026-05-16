"""nidaqlib — Experiment-facing NI-DAQmx acquisition layer.

`nidaqlib` is not a replacement for NI's `nidaqmx-python`. It is a typed,
lifecycle-managed acquisition layer built on top of it, designed to fit the
same scientific-instrumentation ecosystem as `alicatlib` and `sartoriuslib`.

Core API is ``async`` (built on ``anyio``); a sync facade is available at
:mod:`nidaqlib.sync` for scripts, notebooks, and REPL use.

See ``docs/design.md`` for the architectural design.
"""

from __future__ import annotations

from nidaqlib.channels import (
    AnalogInputBase,
    AnalogInputVoltage,
    AnalogOutputVoltage,
    ChannelSpec,
    CounterEdgeCountInput,
    CounterFrequencyInput,
    CounterPeriodInput,
    CounterPulseFrequency,
    CounterPulseTicks,
    CounterPulseTime,
    DigitalInput,
    DigitalOutput,
    ThermocoupleInput,
)
from nidaqlib.config import NidaqConfig, config_from_env
from nidaqlib.constants import (
    ADCTimingMode,
    AutoZeroType,
    CJCSource,
    LoggingMode,
    LoggingOperation,
    TemperatureUnits,
    TerminalConfiguration,
    ThermocoupleType,
)
from nidaqlib.errors import (
    ErrorContext,
    NIDaqBackendError,
    NIDaqConfigurationError,
    NIDaqConfirmationRequiredError,
    NIDaqConnectionError,
    NIDaqDependencyError,
    NIDaqError,
    NIDaqReadError,
    NIDaqResourceError,
    NIDaqSinkDependencyError,
    NIDaqSinkError,
    NIDaqSinkSchemaError,
    NIDaqSinkWriteError,
    NIDaqTaskStateError,
    NIDaqTimeoutError,
    NIDaqTransientError,
    NIDaqValidationError,
    NIDaqWriteError,
    ProtocolKind,
)
from nidaqlib.manager import DaqManager, DeviceResult
from nidaqlib.sinks.base import block_to_rows, reading_to_row
from nidaqlib.streaming import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    PollSource,
    PollSourceAdapter,
    Recording,
    record,
    record_polled,
)
from nidaqlib.system import (
    DeviceInfo,
    DiscoveryResult,
    NIDaqDiscoveryResult,
    find_devices,
)
from nidaqlib.tasks import (
    AcquisitionMode,
    DaqBlock,
    DaqReading,
    DaqSession,
    DeviceSnapshot,
    Edge,
    NIDaqSnapshot,
    TaskBuilder,
    TaskSpec,
    TaskState,
    Timing,
    open_device,
)
from nidaqlib.tasks.metadata import RunMetadata, read_sidecar, sidecar_path_for, write_sidecar
from nidaqlib.tasks.spec import TdmsLogging
from nidaqlib.tasks.triggers import (
    AnalogEdgeStartTrigger,
    AnalogTriggerSlope,
    DigitalEdgeReferenceTrigger,
    DigitalEdgeStartTrigger,
    TriggerSpec,
)
from nidaqlib.units import to_pint
from nidaqlib.version import __version__

__all__ = [
    "ADCTimingMode",
    "AcquisitionMode",
    "AcquisitionSummary",
    "AnalogEdgeStartTrigger",
    "AnalogInputBase",
    "AnalogInputVoltage",
    "AnalogOutputVoltage",
    "AnalogTriggerSlope",
    "AutoZeroType",
    "CJCSource",
    "ChannelSpec",
    "CounterEdgeCountInput",
    "CounterFrequencyInput",
    "CounterPeriodInput",
    "CounterPulseFrequency",
    "CounterPulseTicks",
    "CounterPulseTime",
    "DaqBlock",
    "DaqManager",
    "DaqReading",
    "DaqSession",
    "DeviceInfo",
    "DeviceResult",
    "DeviceSnapshot",
    "DigitalEdgeReferenceTrigger",
    "DigitalEdgeStartTrigger",
    "DigitalInput",
    "DigitalOutput",
    "DiscoveryResult",
    "Edge",
    "ErrorContext",
    "ErrorPolicy",
    "LoggingMode",
    "LoggingOperation",
    "NIDaqBackendError",
    "NIDaqConfigurationError",
    "NIDaqConfirmationRequiredError",
    "NIDaqConnectionError",
    "NIDaqDependencyError",
    "NIDaqDiscoveryResult",
    "NIDaqError",
    "NIDaqReadError",
    "NIDaqResourceError",
    "NIDaqSinkDependencyError",
    "NIDaqSinkError",
    "NIDaqSinkSchemaError",
    "NIDaqSinkWriteError",
    "NIDaqSnapshot",
    "NIDaqTaskStateError",
    "NIDaqTimeoutError",
    "NIDaqTransientError",
    "NIDaqValidationError",
    "NIDaqWriteError",
    "NidaqConfig",
    "OverflowPolicy",
    "PollSource",
    "PollSourceAdapter",
    "ProtocolKind",
    "Recording",
    "RunMetadata",
    "TaskBuilder",
    "TaskSpec",
    "TaskState",
    "TdmsLogging",
    "TemperatureUnits",
    "TerminalConfiguration",
    "ThermocoupleInput",
    "ThermocoupleType",
    "Timing",
    "TriggerSpec",
    "__version__",
    "block_to_rows",
    "config_from_env",
    "find_devices",
    "open_device",
    "read_sidecar",
    "reading_to_row",
    "record",
    "record_polled",
    "sidecar_path_for",
    "to_pint",
    "write_sidecar",
]
