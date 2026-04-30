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
    NIDaqValidationError,
    NIDaqWriteError,
)
from nidaqlib.manager import DaqManager, DeviceResult
from nidaqlib.streaming import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    record,
    record_polled,
)
from nidaqlib.tasks import (
    AcquisitionMode,
    DaqBlock,
    DaqReading,
    DaqSession,
    Edge,
    TaskBuilder,
    TaskSpec,
    Timing,
    open_device,
)
from nidaqlib.tasks.metadata import RunMetadata, read_sidecar, sidecar_path_for, write_sidecar
from nidaqlib.tasks.models import DaqSample
from nidaqlib.tasks.spec import TdmsLogging
from nidaqlib.tasks.triggers import (
    AnalogEdgeStartTrigger,
    AnalogTriggerSlope,
    DigitalEdgeReferenceTrigger,
    DigitalEdgeStartTrigger,
    TriggerSpec,
)
from nidaqlib.version import __version__

__all__ = [
    "AcquisitionMode",
    "AcquisitionSummary",
    "AnalogEdgeStartTrigger",
    "AnalogInputVoltage",
    "AnalogOutputVoltage",
    "AnalogTriggerSlope",
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
    "DaqSample",
    "DaqSession",
    "DeviceResult",
    "DigitalEdgeReferenceTrigger",
    "DigitalEdgeStartTrigger",
    "DigitalInput",
    "DigitalOutput",
    "Edge",
    "ErrorContext",
    "ErrorPolicy",
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
    "NidaqConfig",
    "OverflowPolicy",
    "RunMetadata",
    "TaskBuilder",
    "TaskSpec",
    "TdmsLogging",
    "ThermocoupleInput",
    "Timing",
    "TriggerSpec",
    "__version__",
    "config_from_env",
    "open_device",
    "read_sidecar",
    "record",
    "record_polled",
    "sidecar_path_for",
    "write_sidecar",
]
