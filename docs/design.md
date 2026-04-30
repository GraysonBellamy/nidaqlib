# `nidaqlib` Design Document

**Status:** Definitive design (v1) — supersedes prior analysis notes  
**Target package name:** `nidaqlib`  
**Proposed role:** Experiment-facing NI-DAQmx acquisition layer for the existing `alicatlib` / `sartoriuslib` ecosystem  
**Primary dependency:** [`nidaqmx-python`](https://github.com/ni/nidaqmx-python)  
**Authoring context:** Designed as a sibling package to [`alicatlib`](https://github.com/GraysonBellamy/alicatlib) and [`sartoriuslib`](https://github.com/GraysonBellamy/sartoriuslib)

---

## Executive Summary

Building a `nidaqlib` package is worthwhile, but only if it is deliberately positioned as an **opinionated experiment/acquisition layer** rather than a replacement for `nidaqmx-python`.

Unlike `alicatlib` and `sartoriuslib`, a DAQ package cannot own the low-level protocol. Alicat and Sartorius instruments expose serial protocols that can be implemented directly. NI-DAQmx devices, by contrast, depend on NI's proprietary driver stack, C API, and platform-specific runtime. The Python package `nidaqmx-python` already provides the correct low-level interface to that stack and should remain the foundation.

The value of `nidaqlib` should therefore be:

> Use NI-DAQmx for what only NI can do. Use `nidaqlib` for typed task specifications, acquisition lifecycle, structured errors, normalized sample/block models, ecosystem-compatible logging, and integration with Alicat/Sartorius devices.

The package should not attempt to wrap every NI-DAQmx function. It should expose a small, stable, lab-oriented API that supports common acquisition tasks and integrates naturally with the rest of the ecosystem.

---

## 1. Motivation

The current ecosystem has a clear design identity:

- `alicatlib` provides a typed Python API for Alicat mass flow meters/controllers.
- `sartoriuslib` provides a typed Python API for Sartorius balances.
- Both are async-first, have sync facades, support multi-device acquisition, include fake/test backends, and expose pluggable sinks for logging.
- Both are designed for scientific experiments where timing, provenance, typed data, and long-running reliability matter.

DAQ hardware is a natural next member of this ecosystem. A typical experimental control stack may include:

- NI DAQ thermocouple or voltage measurements.
- Analog outputs for control signals.
- Digital outputs for relays/triggers.
- Counter inputs for encoders or frequency signals.
- Alicat mass flow controllers.
- Sartorius balances.
- Shared experiment metadata and unified run logging.

Without a wrapper, users must combine very different programming models:

```python
# nidaqlib ecosystem style
async with await open_device(...) as dev:
    frame = await dev.poll()

# Raw NI-DAQmx style
with nidaqmx.Task() as task:
    task.ai_channels.add_ai_voltage_chan(...)
    task.timing.cfg_samp_clk_timing(...)
    values = task.read(...)
```

That mismatch matters. It creates duplicated lifecycle code, ad hoc data formatting, inconsistent error handling, inconsistent logging, and inconsistent testability.

`nidaqlib` should close that gap.

---

## 2. Core Recommendation

Build `nidaqlib`, but keep the package intentionally narrow.

### What it should be

`nidaqlib` should be:

- A typed task-specification layer over `nidaqmx-python`.
- A lifecycle-managed task/session abstraction.
- A normalized acquisition and logging layer.
- A bridge between NI-DAQmx and the existing `alicatlib` / `sartoriuslib` ecosystem.
- A package with hardware-free tests via a fake DAQ backend.
- A package that keeps NI concepts visible enough that NI documentation remains useful.

### What it should not be

`nidaqlib` should **not** be:

- A full reimplementation of NI-DAQmx.
- A giant wrapper around every method in `nidaqmx-python`.
- A transport/protocol package.
- A replacement for NI TDMS logging.
- An abstraction so generic that it hides important DAQ concepts.
- A framework that treats all DAQ acquisition as scalar polling.

The design should be ruthless about scope.

---

## 3. Ecosystem Comparison

### 3.1 Existing package design patterns

Both `alicatlib` and `sartoriuslib` share a recognizable architecture:

| Pattern | `alicatlib` | `sartoriuslib` | Should `nidaqlib` keep it? |
|---|---:|---:|---:|
| Async-first API | Yes | Yes | Yes, with thread-backed NI calls |
| Sync facade | Yes | Yes | Yes |
| Typed public models | Yes | Yes | Yes |
| Structured errors | Yes | Yes | Yes |
| Manager for multiple devices | Yes | Yes | Yes, but for tasks |
| Pluggable sinks | Yes | Yes | Yes |
| Fake backend for testing | Yes, fake transport | Yes, fake transport | Yes, fake DAQ backend |
| Hardware tests gated by markers/env vars | Yes | Yes | Yes |
| Safety gates for destructive/stateful operations | Yes | Yes | Yes |
| Protocol implementation | Yes | Yes | No |
| Transport abstraction | Yes | Yes | No, not in the same sense |
| Command catalog | Yes | Yes | No |

### 3.2 Main architectural asymmetry

The existing packages own the wire-level protocol:

```text
transport -> protocol -> command -> session -> device -> recorder/sinks
```

That is appropriate for serial instruments.

DAQ should instead use:

```text
TaskSpec -> TaskBuilder -> DaqSession -> recorder/sinks
```

The low-level command/protocol/device API is already owned by NI and exposed through `nidaqmx-python`.

---

## 4. Design Goals

1. **Preserve NI-DAQmx correctness.**  
   Do not obscure or reimplement driver behavior. Delegate low-level operations to `nidaqmx-python`.

2. **Provide a stable experiment-facing API.**  
   Users should be able to define tasks declaratively and run them consistently.

3. **Integrate with existing ecosystem logging.**  
   DAQ data should be loggable alongside Alicat and Sartorius data.

4. **Support both low-rate and high-rate acquisition.**  
   Scalar polling and hardware-clocked block acquisition require different models.

5. **Make lifecycle explicit.**  
   Task creation, configuration, start, stop, read/write, and close should be managed safely.

6. **Use typed models at package boundaries.**  
   Public inputs and outputs should use frozen dataclasses, enums, protocols, and precise types.

7. **Make high-rate data efficient.**  
   Avoid flattening large DAQ arrays into scalar sample rows unless the user explicitly wants that.

8. **Be testable without hardware.**  
   Most behavior should be tested against a fake backend.

9. **Avoid leaky concurrency.**  
   NI calls are synchronous; async wrapping must be coarse-grained and controlled.

10. **Keep escape hatches.**  
    Users should always be able to access the underlying `nidaqmx.Task` when necessary.

---

## 5. Non-Goals

- No replacement for `nidaqmx-python`.
- No direct binding to the NI C API.
- No custom implementation of NI task scheduling, buffering, or triggering.
- No full coverage of every NI channel type in v1.
- No attempt to support unsupported NI hardware.
- No GUI.
- No RPC/server layer.
- No ORM.
- No forced dependency on pandas.
- No assumption that all DAQ data should become CSV rows.
- No abstraction that prevents users from consulting NI documentation.

---

## 6. Proposed Package Layout

This layout captures the intended package organization. The on-disk tree should
stay close to it as the library evolves.

```text
src/
  nidaqlib/
    __init__.py
    py.typed
    config.py
    errors.py
    _logging.py
    _runtime.py            # eager_task_factory installer (port from alicatlib)

    backend/               # the seam that replaces transport/ in siblings
      __init__.py
      base.py              # DaqBackend Protocol
      nidaqmx_backend.py   # NidaqmxBackend (real)
      fake.py              # FakeDaqBackend (re-exported from testing.py)

    system/
      __init__.py
      discovery.py
      models.py

    channels/
      __init__.py
      base.py
      analog_input.py
      analog_output.py
      digital_input.py
      digital_output.py
      counter_input.py
      counter_output.py

    tasks/
      __init__.py
      spec.py
      builder.py
      session.py
      models.py
      timing.py
      triggers.py
      metadata.py          # RunMetadata + sidecar serialization

    streaming/
      __init__.py
      block.py             # record() — hardware-clocked block path
      sample.py            # DaqSample
      recorder.py          # record_polled() — software-timed scalar path

    sinks/
      __init__.py
      base.py              # ReadingSink, SampleSink, BlockSink Protocols
      _schema.py           # sample_to_row, reading_to_row, block_to_long_rows
      memory.py
      csv.py
      jsonl.py
      sqlite.py
      parquet.py
      postgres.py

    manager.py

    sync/
      __init__.py
      portal.py

    cli/
      __init__.py
      list.py              # nidaq-list   (v0.1)
      capture.py           # nidaq-capture (v0.1)
      read.py              # nidaq-read   (v0.2)
      info.py              # nidaq-info   (v0.2)

    testing.py             # FakeDaqBackend convenience builders
```

The placement of `DaqSession` under `tasks/session.py` (not `devices/session.py`) is a deliberate deviation from the sibling convention. The central abstraction in NI-DAQmx is the *task*, not the *device* — channels are added to tasks, not to devices, and a single physical card can host several independent tasks. Naming the directory `tasks/` matches NI's mental model and avoids the misleading suggestion that one `DaqSession` corresponds to one device.

### Modules intentionally omitted

Unlike `alicatlib` and `sartoriuslib`, this package does **not** include:

```text
transport/    # replaced by backend/ — the seam moves from byte-stream to task-operation
protocol/     # nidaqmx-python is the protocol layer
commands/     # task.ai_channels.add_ai_voltage_chan(...) is already typed and discoverable
registry/     # nidaqmx.constants are re-exported as needed; no parallel codes table
```

Those concepts are either owned by NI-DAQmx or unnecessary at this level. Resist re-introducing them for symmetry's sake — that pressure is what kills DAQ wrappers.

---

## 7. Public API Shape

### 7.1 Basic analog input task

```python
import anyio

from nidaqlib import AnalogInputVoltage, TaskSpec, open_device


spec = TaskSpec(
    name="surface_temperatures",
    channels=[
        AnalogInputVoltage(
            physical_channel="Dev1/ai0",
            name="surface_tc_mv",
            min_val=-0.1,
            max_val=0.1,
        ),
        AnalogInputVoltage(
            physical_channel="Dev1/ai1",
            name="back_tc_mv",
            min_val=-0.1,
            max_val=0.1,
        ),
    ],
)


async def main() -> None:
    async with await open_device(spec) as task:
        reading = await task.poll()
        print(reading.values)


anyio.run(main)
```

### 7.2 Continuous hardware-clocked acquisition

```python
import anyio

from nidaqlib import AcquisitionMode, AnalogInputVoltage, TaskSpec, Timing, open_device
from nidaqlib.streaming import record
from nidaqlib.sinks import ParquetSink


spec = TaskSpec(
    name="heat_flux_run",
    channels=[
        AnalogInputVoltage("Dev1/ai0", name="heat_flux", min_val=-10.0, max_val=10.0),
        AnalogInputVoltage("Dev1/ai1", name="surface_tc", min_val=-0.1, max_val=0.1),
    ],
    timing=Timing(
        rate_hz=1000.0,
        mode=AcquisitionMode.CONTINUOUS,
        samples_per_channel=1000,
    ),
)


async def main() -> None:
    async with await open_device(spec) as task:
        async with (
            record(task, chunk_size=1000) as stream,
            ParquetSink("run.parquet") as sink,
        ):
            async for block in stream:
                await sink.write(block)


anyio.run(main)
```

### 7.3 Sync facade

```python
from nidaqlib.sync import Daq


with Daq.open_device(spec) as task:
    block = task.read_block(samples_per_channel=1000)
    print(block.data.shape)
```

### 7.4 Escape hatch

```python
async with await open_device(spec) as task:
    raw_task = task.raw_task
    # Use raw nidaqmx.Task for unsupported advanced features.
```

The escape hatch is important. It prevents the wrapper from becoming a bottleneck for advanced NI features.

---

## 8. Core Data Models

All spec dataclasses use `kw_only=True`. Without it, subclasses (`ThermocoupleInput`) cannot add non-default fields after a parent's defaulted fields, which is the primary failure mode of `dataclass` inheritance. Construction is keyword-only by convention across the public API.

### 8.1 TaskSpec

```python
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskSpec:
    name: str
    channels: Sequence[ChannelSpec]
    timing: Timing | None = None
    trigger: TriggerSpec | None = None
    logging: TdmsLogging | None = None
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)
```

`TaskSpec` is the primary declarative object. It is serializable to/from JSON via `to_dict()` / `from_dict()` (see §18.3) so run metadata can persist it.

### 8.2 ChannelSpec

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelSpec:
    physical_channel: str
    name: str | None = None
    unit: str | None = None
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)
```

Concrete subclasses map to common NI channel creation methods. Each subclass declares a `kind: ClassVar[str]` for use as a discriminator during serialization (e.g., `"ai_voltage"`, `"thermocouple"`).

### 8.3 AnalogInputVoltage

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputVoltage(ChannelSpec):
    kind: ClassVar[str] = "ai_voltage"
    min_val: float = -10.0
    max_val: float = 10.0
    terminal_config: TerminalConfig | None = None
    custom_scale_name: str | None = None
```

### 8.4 ThermocoupleInput

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ThermocoupleInput(ChannelSpec):
    kind: ClassVar[str] = "thermocouple"
    thermocouple_type: ThermocoupleType
    min_val: float
    max_val: float
    cjc_source: CjcSource | None = None
    cjc_val: float | None = None
    units: TemperatureUnit = TemperatureUnit.DEG_C
```

Thermocouples are likely important for the lab use case, but they should not be the very first feature unless needed immediately. Voltage input is a cleaner v0.1 target. (`kw_only=True` plus the absence of defaults on `thermocouple_type`/`min_val`/`max_val` is the canonical pattern for required fields after a defaulted parent.)

### 8.5 Timing

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    rate_hz: float
    mode: AcquisitionMode = AcquisitionMode.CONTINUOUS
    samples_per_channel: int | None = None
    source: str | None = None
    active_edge: Edge = Edge.RISING
```

### 8.6 DaqReading

For scalar or low-rate polling. This is the **cross-instrument bridge** model — the field shape mirrors `alicatlib.Sample` and `sartoriuslib.Sample` closely so DAQ rows land in the same SQLite/Parquet pipeline as flow-controller and balance rows:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DaqReading:
    device: str                                   # manager-add name; matches sibling Sample.device
    task: str | None = None                       # underlying TaskSpec.name (optional second key)
    values: Mapping[str, float | int | bool]      # one entry per channel
    units: Mapping[str, str | None]
    requested_at: datetime                        # wall-clock just before to_thread.run_sync
    received_at: datetime                         # wall-clock just after to_thread.run_sync
    midpoint_at: datetime                         # midpoint of the request/receive window
    monotonic_ns: int                             # monotonic_ns at midpoint
    elapsed_s: float                              # received_at - requested_at, seconds
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    error: NIDaqError | None = None               # populated only under ErrorPolicy.RETURN (see §13.2)
```

Field-naming notes:

- **`device` is the join key**, matching `alicatlib.Sample.device` and `sartoriuslib.Sample.device`. When `DaqReading` is emitted via `DaqManager`, `device` is the manager-add name. When emitted directly from a `DaqSession` without a manager, `device` falls back to `spec.name`.
- **`elapsed_s`** matches sartoriuslib's field name (alicatlib uses `latency_s` for the same quantity — that divergence is captured in §8.8).
- **`requested_at` / `received_at` / `midpoint_at`** are wall-clock provenance for cross-instrument latency analysis. Use `monotonic_ns` for scheduling and join arithmetic; wall-clock fields are not monotonic across clock adjustments.

### 8.7 DaqBlock

For hardware-clocked acquisition:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DaqBlock:
    device: str                          # manager-add name, or falls back to spec.name
    task: str | None = None              # underlying TaskSpec.name
    channels: tuple[str, ...]            # in array-row order
    data: np.ndarray                     # INVARIANT: shape == (len(channels), samples_per_channel),
                                         #            dtype float64 for AI voltage / TC
    block_index: int                     # 0-based, monotonic per task; resets only on new task
    first_sample_index: int              # cumulative sample offset since task_started_at
    samples_per_channel: int             # data.shape[1]
    sample_rate_hz: float | None         # from Timing.rate_hz (None for on-demand reads)
    dt_s: float | None                   # 1 / sample_rate_hz
    task_started_at: datetime            # wall-clock anchor for time_s reconstruction
    t0: datetime                         # provenance: wall-clock at first sample of THIS block
    monotonic_ns: int                    # provenance: monotonic at read_started_at
    read_started_at: datetime            # provenance, not per-sample truth
    read_finished_at: datetime           # provenance, not per-sample truth
    elapsed_s: float                     # read_finished_at - read_started_at
    units: Mapping[str, str | None]      # keyed by channel name
    error: NIDaqError | None = None      # populated only under ErrorPolicy.RETURN (see §13.2)
```

**Sample-time reconstruction.** The hardware sample clock owns true sample timing — wall-clock fields are provenance only and carry OS-scheduler jitter. To reconstruct the timestamp of sample *k* within a block (where *k* is `0..samples_per_channel-1`):

```python
absolute_sample_index = block.first_sample_index + k
time_since_task_start = absolute_sample_index / block.sample_rate_hz
sample_wall_clock = block.task_started_at + timedelta(seconds=time_since_task_start)
```

Do **not** interpolate sample times off `t0` or `read_started_at` — those drift block-to-block. The hardware clock guarantees uniform `dt_s` between samples within a single task; the wrapper records that guarantee by anchoring on `task_started_at` + `first_sample_index`.

`DaqBlock` is the preferred model for real DAQ acquisition. It preserves the natural shape of the data and avoids prematurely converting high-rate arrays into rows. Sinks that need scalar rows opt in via `block_to_long_rows()` (see §14.1).

### 8.8 Note on ecosystem `Sample` parity

A reasonable instinct is to make `nidaqlib` emit the same `Sample` row that `alicatlib` and `sartoriuslib` emit, so a single sink table can carry all three. Two reasons not to force this:

1. **The ecosystem schemas have already diverged.** There is no shared row schema to preserve. The actual divergence:

| Field             | `alicatlib.Sample` | `sartoriuslib.Sample` | `watlowlib.Sample`              | `DaqReading` (this design)         |
|-------------------|--------------------|-----------------------|---------------------------------|------------------------------------|
| `device`          | str                | str                   | str                             | str                                |
| second key        | `unit_id: str`     | —                     | `address: int`                  | `task: str \| None`                |
| protocol marker   | —                  | `protocol`            | `protocol`                      | —                                  |
| payload           | `frame: DataFrame` | `reading: Reading?`   | per-parameter scalar columns    | `values: Mapping[str, scalar]`     |
| latency field     | `latency_s`        | `elapsed_s`           | `latency_s`                     | `elapsed_s` (matches sartoriuslib) |
| `requested_at`    | ✓                  | ✓                     | ✓                               | ✓                                  |
| `received_at`     | ✓                  | ✓                     | ✓                               | ✓                                  |
| `midpoint_at`     | ✓                  | ✓                     | ✓                               | ✓                                  |
| `monotonic_ns`    | ✓                  | ✓                     | ✓                               | ✓                                  |
| `metadata`        | —                  | `Mapping[str, str]`   | —                               | `Mapping[str, scalar]`             |
| `error`           | —                  | `SartoriusError?`     | —                               | `NIDaqError?`                      |
| raw bytes         | —                  | —                     | `raw: bytes`                    | —                                  |

   `DaqReading` aligns with sartoriuslib (the more recent of the two original libs) where they disagree, and adds nothing that prevents joining against an alicatlib or watlowlib row on `(device, monotonic_ns)`.

2. **Hardware-clocked DAQ is rectangular by nature.** A 1 kHz × 8-channel block is `(8, 1000)` of float64. Fanning that into 8000 dataclass instances per second to satisfy a `Sample`-shaped pipeline burns CPU and discards the natural shape that Parquet row-groups, NumPy slicing, and TDMS all want.

The right bridge is `DaqReading` (low-rate, scalar, ecosystem-friendly) for cross-instrument correlation, and `DaqBlock` (rectangular, hardware-clocked) for the high-rate path that has no analog in the serial-instrument libs. `DaqSample` exists for the rare case where a user wants per-sample scalarization (e.g., debugging, very low-rate logging into a CSV alongside Alicat rows).

### 8.9 DaqSample

Optional scalarized row model:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DaqSample:
    device: str                          # join key matches DaqReading
    task: str | None = None
    channel: str
    value: float | int | bool
    acquired_at: datetime
    monotonic_ns: int
    unit: str | None
    error: NIDaqError | None = None
```

This is useful for slow data and common sinks, but should not be the default internal representation for high-rate acquisition. `DaqSample` rows are produced explicitly via `block_to_long_rows(block)` (see §14.1) — never automatically.

---

## 9. Session and Lifecycle Model

### 9.1 DaqSession

```python
class DaqSession:
    def __init__(
        self,
        spec: TaskSpec,
        backend: DaqBackend,
        *,
        timeout: float = 10.0,
    ) -> None:
        ...

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def poll(self, *, timeout: float | None = None) -> DaqReading:
        """One-shot scalar read across all channels.

        Valid only for tasks that are NOT actively buffering (i.e., no hardware
        sample clock running, no `record()` consumer attached). Calling poll()
        on a task in `AcquisitionMode.CONTINUOUS` while `record()` is draining
        the NI buffer raises NIDaqTaskStateError — the two consumers would
        compete for samples and the answer would be undefined.

        For the live-scalar use case during a high-rate acquisition (e.g., a UI
        showing the latest value), use `record()` and read the most recent
        DaqBlock's last column.
        """
        ...

    async def read_block(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,
    ) -> DaqBlock:
        ...

    async def write(self, values: Mapping[str, float | bool]) -> None:
        ...

    async def close(self) -> None:
        ...

    @property
    def raw_task(self) -> nidaqmx.Task:
        ...
```

### 9.2 Lifecycle invariants

- A task is created once per session.
- Configuration occurs before start.
- Reads and writes are guarded by a session lock.
- `close()` is idempotent.
- `__aexit__` always attempts to stop and close.
- Failed configuration tears down the task.
- Any wrapped NI exception includes task name, channel name when known, operation, and underlying NI error code when available.
- The raw `nidaqmx.Task` is available as an escape hatch, but use of it is caller-owned.
- **`poll()` is invalid mid-buffered-acquisition.** It raises `NIDaqTaskStateError` if the task is configured `CONTINUOUS` or `FINITE` with a sample clock and is in the started state. This avoids competing consumers on the same NI buffer.
- **Callback-bridge shutdown is ordered** (see §11.3.2). For sessions using the every-N-samples callback bridge, `close()` MUST: (1) unregister the NI callback, (2) post a sentinel to the chunk queue, (3) await drain exit, (4) stop the task, (5) close the task, (6) close the BlockingPortal — in that order. Reordering invites a stopped-task callback (driver crash) or a deadlock in the drain coroutine.

### 9.3 Open factory

```python
async def open_device(
    spec: TaskSpec,
    *,
    backend: DaqBackend | None = None,
    timeout: float = 10.0,
    autostart: bool = True,
    confirm_start: bool = False,
) -> DaqSession:
    ...
```

This mirrors the ecosystem style of `open_device(...)`, but the object
being opened is a DAQ task, not a physical serial device. The factory is
a plain async function: callers `await` it to receive a configured
`DaqSession`, and the session itself is the async context manager:

```python
async with await open_device(spec) as session:
    ...
```

`autostart=False` returns a configured-but-not-started session for
recorder paths that need to register callbacks before `task.start()`.
`confirm_start=True` is required for task starts that actuate hardware,
such as counter-output pulse trains.

---

## 10. Backend Abstraction

### 10.1 Why a backend, not a transport

For Alicat/Sartorius, a fake transport works because the package owns the bytes on the wire.

For DAQ, there are no serial bytes to fake. The underlying interface is `nidaqmx.Task` and the NI driver. Therefore, testing should use a fake backend.

### 10.2 Backend protocol

```python
class DaqBackend(Protocol):
    def create_task(self, name: str) -> Any:
        ...

    def close_task(self, task: Any) -> None:
        ...

    def add_channel(self, task: Any, spec: ChannelSpec) -> None:
        ...

    def configure_timing(self, task: Any, timing: Timing) -> None:
        ...

    def start_task(self, task: Any) -> None:
        ...

    def stop_task(self, task: Any) -> None:
        ...

    def read_block(
        self,
        task: Any,
        samples_per_channel: int,
        timeout: float,
    ) -> np.ndarray:
        ...

    def write(
        self,
        task: Any,
        values: Mapping[str, float | bool],
        timeout: float,
    ) -> None:
        ...
```

### 10.3 Real backend

```python
class NidaqmxBackend:
    """Backend that delegates to nidaqmx-python."""
```

Responsibilities:

- Create `nidaqmx.Task`.
- Add channels from `ChannelSpec`.
- Configure timing and triggers.
- Create NI stream readers/writers where appropriate.
- Convert NI exceptions into `NIDaqError` subclasses.
- Preserve underlying exception as `__cause__`.

### 10.4 Fake backend

```python
class FakeDaqBackend:
    """Deterministic backend for tests and examples."""
```

Capabilities:

- Scripted scalar reads.
- Scripted block reads.
- Simulated timeouts.
- Simulated device errors.
- Simulated finite acquisition completion.
- Optional deterministic waveform generation.
- Operation log for assertions.

Example:

```python
backend = FakeDaqBackend(
    blocks={
        "heat_flux_run": [
            np.zeros((2, 1000)),
            np.ones((2, 1000)),
        ],
    }
)
```

---

## 11. Async Strategy

`nidaqmx-python` is synchronous. The async API must be honest about this.

### 11.1 Use worker threads at coarse boundaries

Good:

```python
block = await anyio.to_thread.run_sync(
    backend.read_block,
    task,
    samples_per_channel,
    timeout,
)
```

Bad:

```python
for i in range(samples_per_channel):
    sample = await anyio.to_thread.run_sync(task.read)
```

### 11.2 Session locking

All task operations should be serialized per session:

```python
async with self._lock:
    return await anyio.to_thread.run_sync(...)
```

This avoids unsafe concurrent calls to the same underlying `nidaqmx.Task`.

### 11.3 Two paths for continuous acquisition

The recorder must support two acquisition models, dispatched on the task's configured timing mode. Forcing one to use the other's pattern produces either dropped samples or deadlocks.

#### 11.3.1 Software-timed (low-rate)

For `Timing.rate_hz` low enough that absolute-target scheduling is precise enough (≤ ~100 Hz, typical for thermocouple boards or scalar polling):

```text
anyio.sleep_until(target[n]) -> to_thread.run_sync(task.read) -> DaqReading
```

This mirrors the `record()` loop in `alicatlib.streaming.recorder` exactly: compute `target[n] = start + n * (1/rate_hz)`, sleep, poll, emit. Drift compounds linearly only against the host clock, which is fine at low rates.

#### 11.3.2 Hardware-timed (high-rate)

For continuous tasks driven by the NI sample clock, the producer pattern is:

```text
NI hardware clock -> NI buffer -> blocking read_many_sample -> DaqBlock stream
```

There are two ways to drive the read loop. Both are valid; pick one per recorder.

**Option A — blocking read in a worker thread (simpler):**

```python
async def _producer() -> None:
    while not stop.is_set():
        block = await anyio.to_thread.run_sync(
            backend.read_block, task, chunk_size, timeout
        )
        await tx.send(block)
```

This is the recommended default. NI's `read_many_sample` blocks until `chunk_size` samples are available, so the worker thread is parked in the driver — no busy loop, no software cadence.

**Option B — `register_every_n_samples_acquired_into_buffer_event` callback (lower latency, harder to get right):**

NI exposes a driver callback that fires every N samples. This callback **runs on a DAQmx driver thread, not the asyncio event loop.** AnyIO/asyncio APIs are unsafe to call from it. The bridge must be a thread-safe, lock-free hand-off, with a sentinel-based shutdown to defeat anyio's cancellation shielding on `to_thread.run_sync`.

```python
import queue
from anyio.from_thread import BlockingPortal

_SENTINEL: object = object()  # private; identity check, not value check

# In the async setup:
portal = await BlockingPortal.__aenter__(...)        # owned by the recorder
chunk_q: queue.SimpleQueue[np.ndarray | object] = queue.SimpleQueue()
drain_done = anyio.Event()

def _on_buffer_event(task_handle, every_n_samples_event_type, n_samples, callback_data):
    # Runs on a DAQmx driver thread. Do the cheapest possible work.
    arr = task.read(number_of_samples_per_channel=n_samples, timeout=0.0)
    chunk_q.put_nowait(arr)
    return 0  # NI requires int return

task.register_every_n_samples_acquired_into_buffer_event(chunk_size, _on_buffer_event)

# In an anyio task:
async def _drain() -> None:
    try:
        while True:
            arr = await anyio.to_thread.run_sync(chunk_q.get)
            if arr is _SENTINEL:
                return
            await tx.send(_to_block(arr))
    finally:
        drain_done.set()
```

#### Rules for this seam

- **No `anyio.*` calls from `_on_buffer_event`.** Use `queue.SimpleQueue` (thread-safe, no asyncio dependency) or `BlockingPortal.start_task_soon` if you must touch the event loop.
- The callback must be short. Heavy work (numpy reshaping, sink writes) belongs on the consumer side.
- Keep a strong reference to the callback for the lifetime of the task; NI stores it as a raw C function pointer and Python GC will silently break the seam otherwise. `nidaqmx.Task` uses `__slots__` so the wrapper cannot be stashed on the task object — keep it on the backend instance, keyed by `id(task)`.

#### Startup protocol (mandatory — NI ordering)

NI requires `register_every_n_samples_acquired_into_buffer_event` to be called **before** `task.start()`. A registration on a running task is rejected with status code **-200960** ("Register all your DAQmx software events prior to starting the task"). The fake backend mirrors this invariant; the unit suite would otherwise pass while the real NI driver rejects the same code.

The bridge therefore needs a `DaqSession` in the **configured-but-not-yet-started** state at registration time. `DaqSession` exposes this seam via `configure()` (allocates the NI task, applies channels / timing / logging / triggers) separately from `start()` (issues `task.start()`). `open_device(spec, autostart=False)` yields a configured-not-started session for this exact path.

```python
# 1. configure_sync — channels, timing, logging, triggers; raw_task is now usable.
await session.configure()

# 2. register the buffer event while the task is still stopped.
backend.register_every_n_samples(session.raw_task, chunk_size, _on_buffer_event)

# 3. start — first callback fires shortly after.
await session.start()
```

`record(source, use_callback_bridge=True)` validates `source.is_configured and not source.is_started`, then runs steps 2 and 3 internally so the call site stays terse:

```python
async with (
    await open_device(spec, autostart=False) as session,
    record(session, chunk_size=N, use_callback_bridge=True) as (rx, summary),
):
    async for block in rx:
        ...
```

#### Shutdown protocol (mandatory — NI ordering)

`anyio.to_thread.run_sync` does **not** propagate cancellation into the worker thread by default. A `_drain` coroutine awaiting `chunk_q.get()` in a worker is not interrupted by recorder exit — the thread blocks until something arrives. Without an explicit sentinel the close call deadlocks.

NI also rejects the unregister call (`register_every_n_samples_acquired_into_buffer_event(0, None)`) on a running task with status code **-200986** ("DAQmx software event cannot be unregistered because the task is running"). So the on-exit ordering is dictated by NI:

```python
async def _cleanup_on_exit() -> None:
    try:
        await anyio.sleep_forever()  # park until the recorder is cancelled
    finally:
        with anyio.CancelScope(shield=True):
            # 1. Stop the NI task. After this, in-flight callbacks have
            #    completed and no new ones fire — `task.stop()` blocks
            #    until the driver thread quiesces.
            await session.stop()

            # 2. Unregister the buffer event. NI accepts this once the
            #    task is stopped (-200986 only fires while running).
            backend.unregister_every_n_samples(session.raw_task, handle)

            # 3. Wake the drainer.
            chunk_q.put_nowait(_SENTINEL)

            # 4. Wait for the drainer to exit cleanly (no leaked thread).
            await drain_done.wait()
            # 5. session.close() runs in open_device __aexit__ — closes the NI
            #    task. close() checks _started and skips redundant stop.
```

Ordering invariants:

| Ordering requirement | Why |
|---|---|
| Register BEFORE start | NI -200960 — software events must be registered before `task.start()`. |
| Stop BEFORE unregister | NI -200986 — unregister rejected on a running task. `task.stop()` is the synchronisation point that guarantees no new callbacks fire. |
| Unregister BEFORE sentinel | After unregister NI cannot fire a callback that races with the sentinel and orphans an array behind it in the queue. |
| Sentinel BEFORE drain-wait | The drainer is parked in `chunk_q.get()`; only the sentinel wakes it. |
| Drain-wait BEFORE close | The drainer holds the strong reference to the task object; closing while it's mid-iteration is unsafe. |

Get this seam right early. Debugging "callback fired but the stream never got the chunk" hours into a heat-flux run is awful, and debugging "close hangs forever" with no traceback is worse. Both error codes (-200960 on the way in, -200986 on the way out) were observed on the bench day against an NI 9214 — the fake backend now enforces the same ordering so the unit suite catches a regression at unit-test time.

---

## 12. Acquisition Modes

### 12.1 Software-polled acquisition

For slow readings:

```python
async with record_polled(task, rate_hz=10) as stream:
    async for reading in stream:
        ...
```

This mirrors the `alicatlib` / `sartoriuslib` absolute-target recorder.

Use cases:

- Slow analog reads.
- Digital state polling.
- Debugging.
- Low-frequency experiment metadata.
- Integration with Alicat/Sartorius scalar samples.

### 12.2 Hardware-clocked block acquisition

For DAQ-native acquisition:

```python
async with record(task, chunk_size=1000) as stream:
    async for block in stream:
        ...
```

Use cases:

- Thermocouple acquisition.
- Voltage waveforms.
- Heat flux signals.
- High-rate logging.
- Any task where the hardware sample clock should own timing.

### 12.3 Finite acquisition

```python
block = await task.acquire(samples_per_channel=10_000)
```

This should:

1. Configure a finite task.
2. Start the task.
3. Read until complete or timeout.
4. Stop the task.
5. Return one or more `DaqBlock` objects.

### 12.4 Continuous acquisition

```python
async with task.stream(chunk_size=1000) as stream:
    async for block in stream:
        ...
```

This should:

1. Start the task.
2. Repeatedly read blocks.
3. Publish blocks to an AnyIO stream.
4. Stop the task on context exit.
5. Report dropped/late/backpressure events.

---

## 13. Recorder Design

### 13.1 Recorder dispatch

Two public recorders, one for each acquisition model in §11.3:

```python
@asynccontextmanager
async def record_polled(
    source: DaqSession,                                           # DaqManager added in v0.2
    *,
    rate_hz: float,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
) -> AsyncIterator[tuple[AsyncIterator[DaqReading], AcquisitionSummary]]:
    """Software-timed scalar polling. Mirrors alicatlib's record() exactly."""

@asynccontextmanager
async def record(
    source: DaqSession,
    *,
    chunk_size: int,
    timeout: float = 10.0,
    buffer_size: int = 16,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
) -> AsyncIterator[tuple[AsyncIterator[DaqBlock], AcquisitionSummary]]:
    """Hardware-clocked block acquisition. Uses the §11.3.2 producer."""
```

`record_polled` is the bridge into the existing ecosystem `record() + pipe() + SqliteSink` pipeline. `record` is the high-rate path that has no analog in `alicatlib`/`sartoriuslib` and emits `DaqBlock`, not `Sample`.

`record_polled` accepts a direct `DaqSession` for one task or a `DaqManager`
for fan-out across managed tasks.

`AcquisitionSummary` (mirroring sartoriuslib's `AcquisitionSummary`) reports per-run counts:

```python
@dataclass(frozen=True, slots=True)
class AcquisitionSummary:
    blocks_emitted: int        # or readings_emitted for record_polled
    blocks_dropped: int        # 0 unless overflow=DROP_*
    errors_observed: int       # bumped on every wrapped NI error, regardless of policy
    started_at: datetime
    finished_at: datetime
```

The summary is yielded alongside the stream so the consumer can branch on it after the stream closes.

### 13.2 Recorder invariants

- The task is started on recorder entry if not already running.
- The task is stopped on exit if (and only if) the recorder started it.
- A producer task reads blocks.
- Backpressure policy is explicit.
- Each block/reading includes timing metadata.
- The producer never silently drops blocks unless configured to do so.
- Continuous reads use NI/hardware timing, not software sleep loops.
- **TDMS `LoggingMode.LOG`-only is detected and the recorder exits cleanly** (instead of blocking forever in `read_block`). When the task is configured with `LOG` (write-only — samples bypass the application read path), there is no application-visible data; the recorder emits an empty stream, the `AcquisitionSummary` records `blocks_emitted == 0`, and the consumer is responsible for reading the TDMS file directly. This is detected at recorder entry by inspecting `spec.logging.mode` and is not a runtime guess.

#### Error policy semantics

The `error: NIDaq*Error | None` field on `DaqReading` and `DaqBlock` is wired exclusively by the recorder, governed by `error_policy`:

| `error_policy`        | Behavior on wrapped NI error during read     | `record.error` field on emitted records |
|-----------------------|----------------------------------------------|------------------------------------------|
| `ErrorPolicy.RAISE`   | Recorder cancels its task group and re-raises | Always `None`                            |
| `ErrorPolicy.RETURN`  | Recorder emits a record with `.error` set, then continues | Set on error records, `None` on success records |

**Invariants:**

- Under `RAISE`, `record.error` is always `None`. A consumer relying on the field for branching does not need to inspect it.
- Under `RETURN`, the recorder MUST advance the timing fields (`monotonic_ns`, `block_index`, etc.) even on error records, so the consumer can detect dropped intervals. `data` may be a zero-filled or empty array on error blocks; consumers MUST gate on `error is None` before using `data`.
- Sessions (direct `read_block`/`poll`) ALWAYS raise typed errors regardless of any policy — the policy is a recorder-level construct, not a session-level one.
- `DaqManager` layers its own `ErrorPolicy.RAISE | RETURN` over per-task results via `DeviceResult[T]`. The recorder policy and the manager policy compose: a `DaqManager.read_block(...)` under `RETURN` with a recorder under `RAISE` returns `DeviceResult[DaqBlock]` with `.error` set (the raised error becomes a wrapped result).

### 13.3 Overflow policies

```python
class OverflowPolicy(Enum):
    BLOCK = "block"
    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"
```

For DAQ, `BLOCK` is not always safe at high rates because blocking the producer lets the NI buffer overrun. The two recorders therefore default differently:

| Recorder         | Default        | Rationale |
|------------------|----------------|-----------|
| `record_polled`  | `BLOCK`        | Software-timed pollers are slow by definition; sample loss is the bigger user-visible failure than back-pressuring a slow consumer. Matches sibling `record()` in alicatlib/sartoriuslib. |
| `record`         | `DROP_OLDEST`  | Hardware-clocked acquisition cannot pause the NI sample clock. A blocked producer leaks into NI buffer overrun, which surfaces as a confusing `DaqError` minutes or hours later. The user can opt into `BLOCK` once they have measured their consumer throughput. |

Policy meanings:

- `BLOCK`: producer awaits consumer. Preserves every block. May cause NI buffer overrun on hardware-clocked tasks if the consumer is slower than the producer.
- `DROP_NEWEST`: when full, the about-to-be-enqueued block is dropped. Keeps consumer latency bounded; loses freshest data.
- `DROP_OLDEST`: when full, the oldest queued block is evicted. Keeps newest data; loses older queued blocks.

Drops are surfaced via `AcquisitionSummary.blocks_dropped` (or `readings_dropped`) rather than per-block fields — silent loss is never the answer, but per-block plumbing for a counter is overkill. For high-rate durable logging, configure TDMS in addition to the streaming sink — TDMS writes happen on the driver side and are not subject to consumer back-pressure.

---

## 14. Sink Design

### 14.1 Three Sink Protocols, two pipe drivers

Sibling sinks accept a single input type (`Sample`). `nidaqlib` has three input types (`DaqReading`, `DaqSample`, `DaqBlock`) with different shapes and write cadences. Forcing them through one Protocol either burns allocations (wrap each block in a 1-element list) or invites accidental scalarization. The design uses three Protocols and two drivers.

```python
class ReadingSink(Protocol):
    async def open(self) -> None: ...
    async def write_many(self, readings: Sequence[DaqReading]) -> None: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

class SampleSink(Protocol):
    """Same shape as siblings' SampleSink. Accepts DaqSample sequences."""
    async def write_many(self, samples: Sequence[DaqSample]) -> None: ...
    # ... open/close/__aenter__/__aexit__ as above

class BlockSink(Protocol):
    """Block-native — one DaqBlock per call, no Sequence wrapper.
    A DaqBlock is already (n_channels, n_samples); batching it is the wrong axis.
    """
    async def write(self, block: DaqBlock) -> None: ...
    # ... open/close/__aenter__/__aexit__ as above
```

Two drivers thread streams to sinks:

```python
async def pipe(
    stream: AsyncIterator[DaqReading | DaqSample],
    sink: ReadingSink | SampleSink,
    *,
    batch_size: int = 100,
    flush_interval_s: float = 1.0,
) -> None: ...
"""Row-oriented driver. Direct port of the sibling pipe()."""

async def pipe_blocks(
    stream: AsyncIterator[DaqBlock],
    sink: BlockSink,
    *,
    flush_interval_s: float | None = None,
) -> None: ...
"""Block-native driver. No batching axis (blocks are already batched).
flush_interval_s, if set, calls sink.flush() between blocks."""
```

`sinks/_schema.py` provides:

- `sample_to_row(sample: DaqSample) -> dict` — direct port of sibling helper.
- `reading_to_row(reading: DaqReading) -> dict` — flattens `values`/`units` to columns.
- `block_to_long_rows(block: DaqBlock) -> Iterator[dict]` — explicit scalarization for users who deliberately want one-row-per-sample. Never invoked automatically by sinks.

Concrete sinks declare which Protocol(s) they implement:

| Sink           | Reading | Sample | Block                                       |
|----------------|:-------:|:------:|:-------------------------------------------:|
| `InMemorySink` | ✓       | ✓      | ✓                                           |
| `CsvSink`      | ✓       | ✓      | ✗ (raises `NIDaqSinkSchemaError`; opt-in via `accept_blocks=True` to call `block_to_long_rows`) |
| `JsonlSink`    | ✓       | ✓      | ✗ (same)                                    |
| `SqliteSink`   | ✓       | ✓      | summary rows only (one row per block, no scalarization) |
| `ParquetSink`  | ✓       | ✓      | ✓ (preferred; row groups per block)         |
| `PostgresSink` | ✓       | ✓      | summary rows only (one row per block, no scalarization) |

The "refuse blocks by default" rule on row-oriented sinks prevents accidental 1-GB CSVs at 10 kHz × 8 channels.

### 14.2 CSV and JSONL

Best for low-rate scalar data.

Options:

- Flatten `DaqReading` into one row per timestamp.
- Flatten `DaqBlock` into many rows only if explicitly requested.
- Refuse high-rate blocks by default to avoid accidental huge files.

### 14.3 SQLite

Good for metadata, scalar data, run events, and low-rate merged ecosystem logs.

Not ideal for raw high-rate DAQ unless chunked carefully.

### 14.4 Parquet

Good default for medium-rate block data.

Possible schema:

```text
run_id
task
channel
sample_index
time_s
value
unit
block_index
block_t0
```

For large data, use row groups per block or per N blocks.

### 14.5 Postgres

Useful for experiment metadata and low-rate/summary data.

Not the first choice for raw high-rate DAQ unless TimescaleDB or chunking is deliberately designed.

### 14.6 TDMS

TDMS is first-class. Do not hand-write TDMS — `nidaqmx-python` already exposes task-level driver-side logging via:

```python
task.in_stream.configure_logging(
    file_path,
    logging_mode=LoggingMode.LOG_AND_READ,        # or LOG (write-only, faster)
    operation=LoggingOperation.OPEN_OR_CREATE,    # or CREATE_OR_REPLACE / CREATE
    group_name="",
)
```

`LoggingMode` and `LoggingOperation` live in `nidaqmx.constants`. Callers
import them from NI directly; `nidaqlib` stores them on `TdmsLogging`
without wrapping them in parallel enums. The wrapper config:

```python
from nidaqmx.constants import LoggingMode, LoggingOperation

@dataclass(frozen=True, slots=True)
class TdmsLogging:
    path: str | Path
    operation: LoggingOperation = LoggingOperation.OPEN_OR_CREATE
    mode: LoggingMode = LoggingMode.LOG_AND_READ
    group_name: str | None = None
```

`LOG_AND_READ` keeps the user's read path working (samples flow into both the TDMS file and the NI buffer for `DaqBlock` emission). `LOG` is faster but bypasses application-level reads — pick it when the user's only consumer is the file.

When `LoggingMode.LOG` (write-only) is configured, the recorder detects this at entry and emits an empty stream rather than blocking forever in `read_block`. See §13.2 — this is a recorder invariant, not a runtime guess.

Then:

```python
spec = TaskSpec(
    name="fast_ai",
    channels=[...],
    timing=Timing(rate_hz=10_000),
    logging=TdmsLogging("run.tdms"),
)
```

For high-rate acquisition, TDMS should be the recommended durable log path. Ecosystem sinks can record metadata, summaries, and synchronized scalar/control data.

---

## 15. Manager Design

### 15.1 DaqManager

```python
class DaqManager:
    def __init__(self, *, error_policy: ErrorPolicy = ErrorPolicy.RAISE) -> None:
        ...

    async def add(
        self,
        name: str,
        spec: TaskSpec,
        *,
        backend: DaqBackend | None = None,
    ) -> DaqSession:
        ...

    async def remove(self, name: str) -> None:
        ...

    def get(self, name: str) -> DaqSession:
        ...

    async def start(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[None]]:
        ...

    async def stop(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[None]]:
        ...

    async def poll(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[DaqReading]]:
        ...

    async def read_block(
        self,
        samples_per_channel: int,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, DeviceResult[DaqBlock]]:
        ...
```

### 15.2 Manager differences from Alicat/Sartorius

Alicat/Sartorius managers need to canonicalize serial ports and serialize devices sharing a bus.

DAQ manager needs to handle:

- Multiple independent NI tasks.
- Tasks sharing the same physical device.
- Tasks competing for reserved resources.
- Start-order and synchronization concerns.
- Hardware-triggered tasks.
- Optional grouped start/stop.

### 15.3 Resource model

A future version should track:

```python
@dataclass(frozen=True, slots=True)
class PhysicalResource:
    device: str
    subsystem: str  # ai, ao, di, do, ci, co
    channels: tuple[str, ...]
```

This can support preflight checks for obvious conflicts, but NI-DAQmx should remain the final authority.

#### Observed NI reservation behaviour

| Module class | Reservation granularity | NI error on conflict | Notes |
|---|---|---|---|
| TC modules (NI 9211 / 9212 / 9213 / 9214) | Whole module | **-50103** "The specified resource is reserved." | Confirmed on NI 9214 hardware day. A second concurrent task targeting any AI channel on a TC-module-with-an-active-task is rejected at `start()`. The manager must serialise per-module, not per-channel, for these. |

The preflight in `DaqManager.add()` only catches exact `(device, physical_channel)` overlap. Module-level reservations (`-50103`) surface only at `start()` and must be handled via `ErrorPolicy.RETURN` or the `start_synchronized` rollback path.

---

## 16. Error Model

### 16.1 Root error

The root is `NIDaqError` (no `Lib` infix). Subclasses follow the sibling pattern: `<RootPrefix><Category>Error`. Sibling roots are `AlicatError`, `SartoriusError`, `WatlowError`; this matches.

```python
class NIDaqError(Exception):
    def __init__(self, message: str, *, context: ErrorContext | None = None) -> None:
        ...
```

### 16.2 Context

```python
@dataclass(frozen=True, slots=True)
class ErrorContext:
    task_name: str | None = None
    channel_name: str | None = None
    physical_channel: str | None = None
    operation: str | None = None
    ni_error_code: int | None = None
    extra: Mapping[str, object] = field(default_factory=dict)
```

### 16.3 Error subclasses

```text
NIDaqError
  NIDaqConfigurationError
  NIDaqValidationError
  NIDaqTaskStateError
  NIDaqReadError
  NIDaqWriteError
  NIDaqTimeoutError
  NIDaqResourceError
  NIDaqBackendError
  NIDaqDependencyError
  NIDaqSinkError
  NIDaqSinkSchemaError
  NIDaqSinkWriteError
```

### 16.4 Wrapping NI errors

All `nidaqmx.errors.DaqError` exceptions should be wrapped with context and preserved as `__cause__`.

Example:

```python
try:
    data = task.read(...)
except nidaqmx.errors.DaqError as exc:
    raise NIDaqReadError(
        "failed to read DAQ block",
        context=ErrorContext(
            task_name=self.spec.name,
            operation="read_block",
            ni_error_code=getattr(exc, "error_code", None),
        ),
    ) from exc
```

---

## 17. Safety Model

DAQ outputs can affect hardware. Treat them with the same seriousness as Alicat setpoints or Sartorius state-changing operations.

### 17.1 Operations requiring confirmation

- Analog output writes above a configured threshold.
- Digital output writes if marked safety-critical.
- Counter output generation.
- Task overwrite/reuse if it would clear an existing task.
- Any calibration-related operation.
- Any operation that toggles physical relays, heaters, valves, igniters, or other actuators.

### 17.2 Example

```python
await task.write(
    {"heater_command": 4.5},
    confirm=True,
)
```

### 17.3 Channel-level safety metadata

```python
@dataclass(frozen=True, slots=True)
class AnalogOutputVoltage(ChannelSpec):
    min_val: float = -10.0
    max_val: float = 10.0
    safe_min: float | None = None
    safe_max: float | None = None
    requires_confirm: bool = True
```

This mirrors the ecosystem principle that safety is part of the public API.

---

## 18. Configuration and Metadata

### 18.1 NidaqConfig

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class NidaqConfig:
    default_timeout_s: float = 10.0          # NI read/write timeout
    default_sample_rate_hz: float = 1000.0   # used when Timing.rate_hz is unset
    default_buffer_size: int = 16            # AnyIO send-stream buffer for record()
    default_chunk_size: int = 1000           # samples per block for record()
    eager_tasks: bool = False                # opt-in to asyncio.eager_task_factory

    def replace(self, **updates: object) -> "NidaqConfig":
        return dataclasses.replace(self, **updates)


def config_from_env(prefix: str = "NIDAQLIB_") -> NidaqConfig:
    """Reads env vars: DEFAULT_TIMEOUT_S, DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_BUFFER_SIZE, DEFAULT_CHUNK_SIZE, EAGER_TASKS.
    Mirrors alicatlib.config.config_from_env."""
```

The fields are deliberately small in scope. Anything that varies per task (channel ranges, trigger sources, TDMS path) belongs on `TaskSpec`, not on `NidaqConfig`.

### 18.2 Run metadata

Every acquisition can emit:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RunMetadata:
    run_id: str
    started_at: datetime
    nidaqlib_version: str
    nidaqmx_python_version: str
    ni_driver_version: str | None
    python_version: str
    platform: str
    task_specs: Mapping[str, TaskSpec]
    user_metadata: Mapping[str, object] = field(default_factory=dict)
```

### 18.3 Serialization

`TaskSpec`, `ChannelSpec` (and subclasses), `Timing`, `TriggerSpec`, `TdmsLogging`, and `RunMetadata` all expose `to_dict()` / `from_dict()` methods for JSON/YAML round-trip. `dataclasses.asdict` alone is insufficient because it can't distinguish `AnalogInputVoltage` from `ThermocoupleInput` after a round-trip — the channel-subclass discriminator is lost.

The pattern:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputVoltage(ChannelSpec):
    kind: ClassVar[str] = "ai_voltage"
    ...

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, **dataclasses.asdict(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "AnalogInputVoltage":
        if data.get("kind") != cls.kind:
            raise NIDaqValidationError(
                f"kind mismatch: expected {cls.kind!r}, got {data.get('kind')!r}"
            )
        payload = {k: v for k, v in data.items() if k != "kind"}
        return cls(**payload)
```

`ChannelSpec.from_dict` dispatches on the discriminator:

```python
_CHANNEL_REGISTRY: dict[str, type[ChannelSpec]] = {}

def _register_channel(cls: type[ChannelSpec]) -> type[ChannelSpec]:
    _CHANNEL_REGISTRY[cls.kind] = cls
    return cls

@classmethod
def from_dict(cls, data: Mapping[str, object]) -> "ChannelSpec":
    kind = data.get("kind")
    if kind not in _CHANNEL_REGISTRY:
        raise NIDaqValidationError(f"unknown channel kind: {kind!r}")
    return _CHANNEL_REGISTRY[kind].from_dict(data)
```

Round-trip is asserted in unit tests for each spec type.

### 18.4 Sidecar metadata

For TDMS logging, write a sidecar:

```text
run.tdms
run.metadata.json
```

or a SQLite DB:

```text
run.tdms
run.sqlite
```

The sidecar contains the `RunMetadata.to_dict()` output:

- Task specs (via the discriminated `to_dict` chain above).
- Channel names, physical channel names, units.
- Scaling metadata.
- NI driver version if available.
- Git commit/project metadata if supplied (via `user_metadata`).
- Experiment operator metadata if supplied (via `user_metadata`).

---

## 19. System Discovery

A small discovery layer is useful, but it should not become a clone of NI MAX.

### 19.1 API

```python
from nidaqlib.system import list_devices, list_physical_channels

devices = list_devices()
channels = list_physical_channels("Dev1")
```

### 19.2 Models

```python
@dataclass(frozen=True, slots=True)
class DeviceInfo:
    name: str
    product_type: str | None
    serial_number: str | None
    ai_physical_channels: tuple[str, ...]
    ao_physical_channels: tuple[str, ...]
    di_lines: tuple[str, ...]
    do_lines: tuple[str, ...]
    ci_physical_channels: tuple[str, ...]
    co_physical_channels: tuple[str, ...]
```

This is enough to support helpful CLI commands and validation.

---

## 20. CLI Tools

Sartorius has useful CLI tools. `nidaqlib` ships a smaller DAQ-focused set:

| CLI            | Purpose                                  |
|----------------|------------------------------------------|
| `nidaq-list`   | List devices and physical channels.      |
| `nidaq-capture`| Short acquisition to file (Parquet/TDMS).|
| `nidaq-read`   | One-shot scalar read.                    |
| `nidaq-info`   | Print driver/backend version info.       |

### 20.1 `nidaq-list` (v0.1)

Lists devices and physical channels.

```bash
nidaq-list
nidaq-list Dev1
```

### 20.2 `nidaq-read` (v0.2)

One-shot scalar read.

```bash
nidaq-read Dev1/ai0 --min -10 --max 10
```

### 20.3 `nidaq-capture` (v0.1)

Short acquisition to file.

```bash
nidaq-capture Dev1/ai0 Dev1/ai1 --rate 1000 --duration 10 --out run.parquet
```

### 20.4 `nidaq-info` (v0.2)

Prints driver/backend info.

```bash
nidaq-info
```

---

## 21. Testing Strategy

### 21.1 Unit tests

Use `FakeDaqBackend`.

Test:

- TaskSpec validation.
- ChannelSpec validation.
- Timing validation.
- Backend call ordering.
- Error wrapping.
- Session lifecycle.
- Recorder backpressure.
- Sink schema behavior.
- Sync facade parity.

### 21.2 Integration tests without hardware

Use fake backend scripts:

```python
backend = FakeDaqBackend.scripted(
    reads=[
        np.zeros((2, 100)),
        np.ones((2, 100)),
    ]
)
```

Test:

- Continuous record loop.
- Finite acquisition.
- Sink writes.
- Manager dispatch.
- Error-as-sample behavior.

### 21.3 Hardware tests

Use pytest markers similar to the existing ecosystem:

```toml
markers = [
    "hardware: requires connected NI DAQ hardware",
    "hardware_stateful: changes task/device state",
    "hardware_output: writes analog/digital/counter output",
    "hardware_destructive: calibration or potentially unsafe operations",
    "slow: excluded from fast CI",
]
```

Environment gates:

```text
NIDAQLIB_ENABLE_HARDWARE_TESTS=1
NIDAQLIB_ENABLE_STATEFUL_TESTS=1
NIDAQLIB_ENABLE_OUTPUT_TESTS=1
NIDAQLIB_ENABLE_DESTRUCTIVE_TESTS=1
```

Hardware test configuration:

```text
NIDAQLIB_TEST_TC_DEVICE=cDAQ1Mod1
NIDAQLIB_TEST_TC_CHANNEL_PRIMARY=cDAQ1Mod1/ai0
NIDAQLIB_TEST_TC_CHANNEL_SECONDARY=cDAQ1Mod1/ai1  # optional
NIDAQLIB_TEST_TC_TYPE=K                           # default K
NIDAQLIB_TEST_TC_RATE_HZ=10                       # default 10
NIDAQLIB_TEST_TC_MIN_DEGC=-50                     # default -50
NIDAQLIB_TEST_TC_MAX_DEGC=200                     # default 200
```

---

## 22. Documentation Plan

### 22.1 Required docs

```text
docs/
  index.md
  quickstart-async.md
  quickstart-sync.md
  task-specs.md
  channels.md
  timing.md
  streaming.md
  logging.md
  tdms.md
  safety.md
  testing.md
  architecture.md
  troubleshooting.md
```

### 22.2 Most important docs

1. **Quickstart: voltage input**
2. **Quickstart: thermocouple input**
3. **Continuous acquisition to Parquet**
4. **High-rate acquisition to TDMS**
5. **DAQ + Alicat + Sartorius unified logging**
6. **How this differs from raw `nidaqmx-python`**
7. **When to use the raw `nidaqmx.Task` escape hatch**

---

## 23. Packaging

### 23.1 pyproject sketch

```toml
[project]
name = "nidaqlib"
dynamic = ["version"]
description = "Experiment-facing NI-DAQmx acquisition layer for scientific instrumentation."
readme = "README.md"
requires-python = ">=3.13"
license = "MIT"
authors = [{ name = "Grayson Bellamy", email = "gbellamy@umd.edu" }]
keywords = ["ni-daqmx", "daq", "data-acquisition", "instrument", "laboratory"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Framework :: AnyIO",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Topic :: Scientific/Engineering",
    "Topic :: System :: Hardware",
    "Typing :: Typed",
]
dependencies = [
    "anyio>=4.13",
    "nidaqmx>=1.0",
    "numpy>=2",
]

[project.optional-dependencies]
parquet = ["pyarrow>=16"]
postgres = ["asyncpg>=0.30"]
docs = [
    "zensical>=0.0.33",
    "mkdocstrings-python>=1.12",
]
```

### 23.2 Dependency note

Unlike `alicatlib` and `sartoriuslib`, this package cannot have a tiny core dependency footprint. NI-DAQmx acquisition naturally requires `nidaqmx-python`, NumPy, and the NI driver runtime.

This is acceptable because the package is explicitly for NI DAQ hardware.

---

## 25. Potential Shared Core Package

After building three packages, repeated code may justify extracting a common package.

Possible name:

```text
instrumentlib-core
```

or:

```text
labacq-core
```

Potential shared components:

- `ErrorPolicy`
- `DeviceResult` / `DeviceResult`
- sink interfaces
- CSV/JSONL/SQLite/Parquet/Postgres sinks
- recorder utility types
- sync portal helpers
- structured logging helpers
- run metadata models

Do **not** extract too early. Let duplication prove itself first. Premature shared-core abstractions can fossilize bad designs.

---

## 26. Key Design Decisions

### Decision 1: Build on `nidaqmx-python`

**Decision:** `nidaqlib` delegates all low-level NI interactions to `nidaqmx-python`.

**Rationale:** NI owns the proprietary driver stack and exposes the supported Python API. Reimplementing this layer is neither practical nor desirable.

### Decision 2: Use task specs, not device facades

**Decision:** The central abstraction is `TaskSpec` / `DaqSession`, not `Device`.

**Rationale:** NI-DAQmx itself is task-centric. A task contains channels, timing, triggers, and streams. This maps naturally to DAQ workflows.

### Decision 3: Support both readings and blocks

**Decision:** Provide both `DaqReading` and `DaqBlock`.

**Rationale:** Scalar readings are convenient for low-rate acquisition and unified ecosystem logging. Blocks are essential for high-rate DAQ.

### Decision 4: Keep TDMS first-class

**Decision:** Expose NI TDMS logging configuration directly.

**Rationale:** High-rate DAQ should use NI-supported efficient logging where appropriate. Ecosystem sinks are still useful for metadata and low/medium-rate data.

### Decision 5: Fake backend instead of fake transport

**Decision:** Tests use `FakeDaqBackend`.

**Rationale:** There is no DAQ byte transport to fake. The backend boundary is the appropriate seam.

### Decision 6: Async API wraps synchronous calls

**Decision:** Use AnyIO and worker threads for blocking NI calls.

**Rationale:** This preserves ecosystem API consistency without pretending NI-DAQmx is natively async.

### Decision 7: Keep raw task escape hatch

**Decision:** `DaqSession.raw_task` exposes the underlying `nidaqmx.Task`.

**Rationale:** NI-DAQmx is too broad to wrap completely. Users need a safe escape path for advanced features.

---

## 27. Risks and Mitigations

### Risk: Wrapper becomes too broad

**Mitigation:** Start with analog input only. Add features only when needed by real experiments.

### Risk: Async wrapper hides blocking behavior

**Mitigation:** Document thread-backed calls clearly. Use coarse-grained `to_thread` boundaries.

### Risk: High-rate logging is inefficient

**Mitigation:** Prefer `DaqBlock` and TDMS for high-rate acquisition. Avoid default scalarization.

### Risk: NI concepts become hidden

**Mitigation:** Keep names close to NI concepts: task, channel, timing, trigger, stream, samples per channel.

### Risk: Fake backend diverges from real NI behavior

**Mitigation:** Hardware smoke tests and realistic fake backend constraints.

### Risk: Resource conflicts are hard to preflight

**Mitigation:** Perform best-effort validation, but let NI-DAQmx remain the final authority.

---

## 28. Public API Surface

The package-level API re-exports the common acquisition, channel, manager,
error, and metadata types:

```python
from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    AnalogOutputVoltage,
    CounterEdgeCountInput,
    CounterFrequencyInput,
    CounterPeriodInput,
    CounterPulseFrequency,
    CounterPulseTicks,
    CounterPulseTime,
    DaqBlock,
    DaqManager,
    DaqReading,
    DaqSample,
    DaqSession,
    DigitalInput,
    DigitalOutput,
    ErrorContext,
    ErrorPolicy,
    NIDaqError,
    NIDaqReadError,
    NIDaqTaskStateError,
    NIDaqTimeoutError,
    NidaqConfig,
    RunMetadata,
    DeviceResult,
    config_from_env,
    TaskSpec,
    TdmsLogging,
    ThermocoupleInput,
    Timing,
    TriggerSpec,
    open_device,
)

from nidaqlib.streaming import (
    AcquisitionSummary,
    OverflowPolicy,
    record,
    record_polled,
)

from nidaqlib.sinks import (
    BlockSink,
    CsvSink,
    InMemorySink,
    JsonlSink,
    ParquetSink,
    ReadingSink,
    SampleSink,
    pipe,
    pipe_blocks,
)

from nidaqlib.testing import (
    FakeDaqBackend,
)

from nidaqlib.sync import (
    Daq,
)
```

---

## 29. README Positioning

Suggested README opening:

```markdown
# nidaqlib

Experiment-facing NI-DAQmx acquisition tools for Python.

`nidaqlib` is not a replacement for NI's `nidaqmx-python`. It is a typed,
lifecycle-managed acquisition layer built on top of it, designed to fit the
same scientific-instrumentation ecosystem as `alicatlib` and `sartoriuslib`.

Use `nidaqlib` when you want:

- declarative task specifications,
- consistent async/sync APIs,
- structured errors,
- block-oriented acquisition,
- TDMS/Parquet/SQLite logging,
- hardware-free tests,
- and unified experiment workflows across DAQ, flow controllers, and balances.
```

---

## 30. Final Recommendation

Build `nidaqlib`.

But build the smallest thing that clearly improves real lab workflows:

1. Typed analog input task specs.
2. Managed task lifecycle.
3. Block acquisition.
4. Structured errors.
5. Fake backend.
6. Ecosystem-compatible sinks.
7. TDMS pass-through.
8. Sync facade.
9. One excellent example combining DAQ + Alicat + Sartorius.

That package would add genuine value without fighting NI's API. It would also give the ecosystem a coherent acquisition story: serial instruments, balances, and DAQ tasks all feeding the same logging and experiment-control layer.

The core design principle should be:

> Wrap workflow, not capability.

Or less politely:

> Do not try to out-NI NI. Make NI usable in your lab stack.

---

## Appendix A: Comparison to Existing Ecosystem Packages

### A.1 `alicatlib`

`alicatlib` owns:

- Serial transport.
- Alicat ASCII protocol.
- Command encoding/decoding.
- Device family classification.
- Streaming mode.
- Multi-device serial-bus management.
- Acquisition and sinks.

This makes sense because Alicat instruments expose a direct serial protocol.

### A.2 `sartoriuslib`

`sartoriuslib` owns:

- Serial transport.
- xBPI protocol.
- SBI protocol.
- Protocol detection.
- Balance semantic facade.
- Autoprint/streaming handling.
- Acquisition and sinks.

This makes sense because Sartorius balances expose serial protocols and the package can provide a protocol-neutral `Balance` API.

### A.3 `nidaqlib`

`nidaqlib` should own:

- Task specs.
- Task lifecycle.
- Acquisition model.
- Data models.
- Logging.
- Error normalization.
- Fake backend.
- Ecosystem integration.

It should not own:

- NI driver calls.
- Device register/protocol behavior.
- Low-level channel implementation.
- Complete NI feature coverage.

---

## Appendix B: Example Internal Builder

```python
class TaskBuilder:
    def __init__(self, backend: DaqBackend) -> None:
        self._backend = backend

    def build(self, spec: TaskSpec) -> BuiltTask:
        task = self._backend.create_task(spec.name)
        try:
            for channel in spec.channels:
                self._backend.add_channel(task, channel)
            if spec.timing is not None:
                self._backend.configure_timing(task, spec.timing)
            if spec.trigger is not None:
                self._backend.configure_trigger(task, spec.trigger)
        except BaseException:
            self._backend.close_task(task)
            raise

        return BuiltTask(task=task, spec=spec)
```

---

## Appendix C: Example Fake Backend

```python
class FakeDaqBackend:
    def __init__(self, *, blocks: Mapping[str, Sequence[np.ndarray]] | None = None) -> None:
        self._blocks = {name: list(values) for name, values in (blocks or {}).items()}
        self.operations: list[tuple[str, str]] = []

    def create_task(self, name: str) -> FakeTask:
        self.operations.append(("create_task", name))
        return FakeTask(name=name)

    def add_channel(self, task: FakeTask, spec: ChannelSpec) -> None:
        self.operations.append(("add_channel", spec.name or spec.physical_channel))
        task.channels.append(spec)

    def configure_timing(self, task: FakeTask, timing: Timing) -> None:
        self.operations.append(("configure_timing", task.name))
        task.timing = timing

    def read_block(self, task: FakeTask, samples_per_channel: int, timeout: float) -> np.ndarray:
        self.operations.append(("read_block", task.name))
        try:
            return self._blocks[task.name].pop(0)
        except (KeyError, IndexError):
            return np.zeros((len(task.channels), samples_per_channel))
```

---

## Appendix D: Example Unified Experiment Sketch

```python
async with (
    AlicatManager(error_policy=ErrorPolicy.RETURN) as mfc_mgr,
    SartoriusManager(error_policy=ErrorPolicy.RETURN) as bal_mgr,
    DaqManager(error_policy=ErrorPolicy.RETURN) as daq_mgr,
):
    await mfc_mgr.add("fuel_mfc", "/dev/ttyUSB0")
    await bal_mgr.add("sample_mass", "/dev/ttyUSB1")
    await daq_mgr.add("thermal_signals", daq_spec)

    async with (
        record_polled(mfc_mgr, rate_hz=2.0) as mfc_stream,
        record_polled(bal_mgr, rate_hz=2.0) as bal_stream,
        record(daq_mgr.get("thermal_signals"), chunk_size=1000) as daq_stream,
        SqliteSink("run.sqlite") as scalar_sink,
        ParquetSink("daq.parquet") as daq_sink,
    ):
        ...
```

This is the strongest reason to build the package: it turns a messy multi-instrument experiment into a coherent acquisition system.

---

## Appendix E: Migration Map from `alicatlib` / `sartoriuslib`

For readers already steeped in the existing two libraries, this is the file-by-file decision: what ports cleanly, what is replaced, what is intentionally absent, and why. The clean-slate package layout in §6 is the destination; this table is the rationale.

| Existing module | Decision for `nidaqlib` | Why |
|---|---|---|
| `_logging.py` | **Direct port.** Change `ROOT = "nidaqlib"`. | Operators already do `logging.getLogger("alicatlib").setLevel(DEBUG)`; nidaqlib should match. |
| `_runtime.py` | **Direct port from `alicatlib`** (sartoriuslib does not have this module). Same eager-task-factory helper. | No NI-specific concerns. |
| `config.py` | **Port the shape, change the fields.** Frozen `NidaqConfig` + `config_from_env("NIDAQLIB_")`. | Fields differ: `default_timeout_s`, `default_sample_rate_hz`, `default_buffer_size`, `default_chunk_size` — no `baud`/`parity`/`port`. |
| `errors.py` | **Port the shape.** `NIDaqError` + `ErrorContext`. Wrap every `nidaqmx.errors.DaqError` / `DaqWarning` at the boundary into a typed subclass with context. | Same `__cause__`-preserving pattern. Drop subclasses that don't apply (no `MediumMismatchError`); add NI-specific ones (`NIDaqResourceError`, `NIDaqBackendError`). |
| `firmware.py` | **Skip.** | nidaqmx exposes `system.driver_version`. There are no commands to gate by firmware. (Note: sartoriuslib's `firmware.py` is already a stub for similar reasons.) |
| `transport/` | **Replaced by `backend/`.** `DaqBackend` Protocol with `create_task` / `add_channel` / `configure_timing` / `start` / `stop` / `read_block` / `write` / `close`. `NidaqmxBackend` is real, `FakeDaqBackend` is for tests. | The *role* is preserved (swappable I/O seam, fake for tests). The *shape* changes — there are no bytes to move. |
| `protocol/` | **Skip entirely.** | `nidaqmx-python` is the protocol layer. Re-implementing it is pure ceremony. |
| `commands/` | **Skip entirely.** | `task.ai_channels.add_ai_voltage_chan(...)` is already typed and discoverable. A `Command` catalog over it adds no value. **Resist this temptation hardest** — symmetry-for-its-own-sake here is what kills the package. |
| `registry/` | **Skip for now.** | Use library-side enums only where they buy JSON round-trips (`AcquisitionMode`, `Edge`, `AnalogTriggerSlope`). NI-owned constants such as `TerminalConfiguration`, `ThermocoupleType`, `LoggingMode`, and `LoggingOperation` are imported from `nidaqmx.constants` directly; don't generate a parallel codes table. |
| `devices/base.py`, `devices/session.py` | **Port the *shape*, not the implementation.** `DaqSession` plays the role of the `Session`/`Device` facade — every operation goes through one dispatch point that captures timing, holds the lock, and wraps errors. | The session no longer holds a serial port + protocol client; it holds a `nidaqmx.Task` (via the backend) + the `TaskSpec`. Note: sartoriuslib's facade is `devices/balance.py`, not `devices/base.py` — the shape generalizes. |
| `devices/factory.py` (`open_device`) | **Direct port.** `await open_device(spec, *, backend=None, timeout=10.0, autostart=True, confirm_start=False)`. | Returns a configured `DaqSession`; the session is the async context manager. The object being opened is a NI task, not a serial device. |
| `devices/discovery.py` | **Direct port, trivial body.** Wraps `nidaqmx.system.System.local()` and per-device `ai_physical_chans` / `ao_physical_chans` / `di_lines` / `do_lines` enumeration into a `DeviceInfo` of the same shape the other libs return. | See §19. |
| `manager.py` | **Direct port.** `DaqManager` with `add` / `remove` / `close`, `ErrorPolicy.RAISE` / `RETURN`, `DeviceResult`. The port-keyed lock becomes a per-Task lock (or per-device lock when serializing tasks that share a card — see §15.3). LIFO unwind, ref-counting, `ExceptionGroup`-on-failure semantics — all identical. | This is one of the cleanest ports. The Manager's job (named-resource lifecycle + group dispatch + structured error handling) is domain-agnostic. |
| `streaming/sample.py` | **Replaced by `streaming/sample.py` (DaqSample) + `streaming/block.py` (DaqBlock).** | The ecosystem `Sample` schemas have already diverged between alicatlib and sartoriuslib (see §8.8). Don't add `device_time` cross-cutting to either lib — it would be `None` 99% of the time and the schemas aren't parity-aligned anyway. `DaqReading` is the cross-instrument bridge. |
| `streaming/recorder.py` | **Port the absolute-target loop for software-timed mode (§11.3.1). Add a hardware-timed path (§11.3.2).** | `record_polled` mirrors alicatlib's loop exactly. `record` is new — it owns the driver-thread → `queue.SimpleQueue` → anyio bridge for hardware-clocked acquisition. |
| `sinks/` (whole tree) | **Direct copy.** Same `SampleSink` / `BlockSink` Protocols, same `pipe()` driver, same `sample_to_row`, same Csv/Jsonl/Sqlite/Parquet/Postgres/Memory implementations. TDMS is configured driver-side via `TdmsLogging`, not as a sink. | This is the single biggest reason to build the package: rows from a NI card land in the same SQLite table shape as rows from a flow controller. Each sink declares which data types it accepts (`DaqReading`, `DaqSample`, `DaqBlock`). |
| `sync/` | **Direct port.** Same `SyncPortal` pattern, blocking `Daq` facade. | The sync facade goes sync → portal → async → `to_thread` → blocking nidaqmx call. It works; document the layering and don't apologize for it. |
| `testing.py` | **Heavy port.** Provides `FakeDaqBackend` convenience builders, scripted block sequences, simulated callback firings for testing the §11.3.2 bridge. | Analog to `FakeTransport` and what makes the test suite not require a DAQ card. |
| (none) | **New: `system/`.** Device discovery + `DeviceInfo` model. | No analog in the existing libs — serial devices don't enumerate. |

### The two non-obvious places to break the pattern

These are flagged here because they will trip every ecosystem-fluent reader:

1. **`transport/` becomes `backend/`.** The seam moves from the byte layer to the task-operation layer. The role (swappable, fake-able, test-friendly) is unchanged.
2. **`Sample` becomes `DaqBlock` + `DaqReading` + optional `DaqSample`.** Hardware-clocked DAQ is rectangular; one-row-per-sample doesn't fit, and the ecosystem `Sample` schemas have already diverged so there's no parity to preserve. `DaqReading` is the bridge type for cross-instrument scalar correlation.

Everything else is a port.

---

## References

- `alicatlib` repository: <https://github.com/GraysonBellamy/alicatlib>
- `sartoriuslib` repository: <https://github.com/GraysonBellamy/sartoriuslib>
- NI `nidaqmx-python` repository: <https://github.com/ni/nidaqmx-python>
- NI `nidaqmx-python` documentation: <https://nidaqmx-python.readthedocs.io/>
