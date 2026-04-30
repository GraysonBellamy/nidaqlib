# Quickstart — async

`nidaqlib` is async-first. This page walks through:

1. Building a `TaskSpec` for one analog-input task.
2. Opening it with `open_device` and reading one block.
3. Running a continuous recorder.
4. Polling a software-timed task at a fixed rate.

Every example here runs against the production NI driver. Substitute
`backend=FakeDaqBackend(...)` to drive the same code without hardware
(see [`testing.md`](testing.md)).

## One-shot read of one block

```python
import anyio
from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    TaskSpec,
    Timing,
    open_device,
)

async def main() -> None:
    spec = TaskSpec(
        name="ai_demo",
        channels=[
            AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0", unit="V"),
            AnalogInputVoltage(physical_channel="Dev1/ai1", name="ch1", unit="V"),
        ],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.FINITE,
                      samples_per_channel=1000),
    )
    async with await open_device(spec) as session:
        block = await session.acquire(samples_per_channel=1000)
        print(block.data.shape, block.sample_rate_hz)

anyio.run(main)
```

`open_device` configures and starts the finite task. `session.acquire`
reads one `DaqBlock` and stops the task afterward. For continuous
acquisition use `record` (below).

## Continuous acquisition with `record`

`record` wraps the recorder loop and yields a stream of `DaqBlock` records
plus a live `AcquisitionSummary`. The summary is mutated in place during
the run; counters are safe to read at any time.

```python
async with await open_device(spec) as session:
    async with record(session, chunk_size=1000) as (stream, summary):
        async for block in stream:
            process(block)
            if summary.blocks_emitted >= 60:
                break
print(f"emitted={summary.blocks_emitted} dropped={summary.blocks_dropped}")
```

Key arguments:

- `chunk_size` — samples per channel per emitted block.
- `error_policy` — `RAISE` (default) cancels on error; `RETURN` emits an
  error-tagged block and continues. See [`streaming.md`](streaming.md)
  for the trade-offs.
- `overflow` — `DROP_OLDEST` (default) is the hardware-clocked safe
  choice; the NI sample clock cannot pause to wait for a slow consumer.

## Software-timed scalar polling with `record_polled`

When the work is "read once per second and append to a SQLite table next
to the Alicat MFC samples," use `record_polled`. It runs an
absolute-target loop at the requested `rate_hz` and emits one
`DaqReading` per tick.

```python
from nidaqlib import record_polled
from nidaqlib.sinks import InMemorySink, pipe

spec = TaskSpec(
    name="slow_ai",
    channels=[AnalogInputVoltage(physical_channel="Dev1/ai0", name="pressure", unit="V")],
)
async with await open_device(spec) as session, InMemorySink() as sink:
    async with record_polled(session, rate_hz=2.0) as (stream, summary):
        await pipe(stream, sink, batch_size=10, flush_interval_s=2.0)
```

`record_polled` uses `OverflowPolicy.BLOCK` by default — software-timed
pollers can pause safely without losing data.

## What about TDMS?

TDMS is configured on the `TaskSpec` as `logging=TdmsLogging(...)`. The
NI driver writes the TDMS file directly; `nidaqlib` stays out of the
hot path. See [`tdms.md`](tdms.md).

## What if I just want to write a script?

Use the [sync facade](quickstart-sync.md) — same shapes, no `await`.
