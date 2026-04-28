# TDMS

TDMS is first-class but driver-side — `nidaqmx-python` already exposes
task-level logging via `task.in_stream.configure_logging(...)`.
`nidaqlib` does **not** hand-write TDMS bytes; it configures NI's logger
and stays out of the hot path.

## `TdmsLogging`

Attach a `TdmsLogging` to your `TaskSpec` and the session calls
`configure_logging` on the underlying NI task as part of `start()`:

```python
from nidaqlib import AnalogInputVoltage, TaskSpec, TdmsLogging, Timing
from nidaqmx.constants import LoggingMode, LoggingOperation

spec = TaskSpec(
    name="fast_ai",
    channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
    timing=Timing(rate_hz=10_000.0),
    logging=TdmsLogging(
        path="run.tdms",
        operation=LoggingOperation.OPEN_OR_CREATE,
        mode=LoggingMode.LOG_AND_READ,
    ),
)
```

Re-import the enums from `nidaqmx.constants` directly — they are NI's
constants and `nidaqlib` does not re-shape them.

## `LOG_AND_READ` vs `LOG`

| Mode            | What happens                                                      | Pick when |
|-----------------|-------------------------------------------------------------------|-----------|
| `LOG_AND_READ`  | Samples flow into both the TDMS file and the application read path. | You want both a durable TDMS file and live `DaqBlock` records. Default. |
| `LOG`           | Samples bypass the application read path entirely. Faster.        | You only want the file. |

`LOG` is meaningfully faster at very high rates because there's no
double-copy through user space. The cost: `read_block` would block
forever waiting on samples that never arrive — so `nidaqlib`'s
`record()` detects `LoggingMode.LOG` at recorder entry and emits an
empty stream rather than deadlocking. The `AcquisitionSummary` reports
`blocks_emitted == 0` and the consumer is responsible for reading the
TDMS file directly.

## Combining TDMS with the streaming sinks

For high-rate durable logging, configure TDMS in addition to a sink —
TDMS writes happen on the driver side and are not subject to consumer
back-pressure. A typical pattern:

- TDMS for the high-rate raw record (`LOG_AND_READ` if you also want a
  scalar tap).
- `SqliteSink` for low-rate scalar metadata (run IDs, polled MFC /
  balance readings, run boundaries).

That gives you a durable raw file plus a queryable cross-instrument log
that joins on `device` + `monotonic_ns`.

## Sidecar metadata

`write_sidecar()` can write a `<base>.metadata.json` sidecar alongside the
`.tdms` file containing the `TaskSpec`, `RunMetadata`, and discovery context.
Use it when you need a portable audit trail for a run.

## Operations

`LoggingOperation` controls how an existing file is treated:

- `OPEN_OR_CREATE` — append if the file exists; create otherwise.
  Default. Safe for incremental runs.
- `CREATE_OR_REPLACE` — create the file; replace if it exists. Use
  when you explicitly want a fresh capture.
- `CREATE` — create only; fail if the file exists. Use as a safety
  catch against accidental overwrites.

## Reading TDMS back

`nidaqlib` does not include a TDMS reader. Use `nptdms` (the most
common Python option) or NI's TDMS Viewer. The structure NI writes is
documented in their reference; group/channel names match the
`TaskSpec.name` and the channel display names from `ChannelSpec.name`
(or `physical_channel` when the name is unset).
