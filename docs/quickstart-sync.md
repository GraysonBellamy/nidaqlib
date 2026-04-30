# Quickstart — sync

The sync facade lives at `nidaqlib.sync` and wraps the async core through
an `anyio.from_thread` blocking portal. Use it from scripts, notebooks,
and REPLs where `await` is awkward.

```python
from nidaqlib import AnalogInputVoltage, TaskSpec, Timing
from nidaqlib.sync import Daq, record

spec = TaskSpec(
    name="ai_demo",
    channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
    timing=Timing(rate_hz=1000.0),
)

with Daq.open_device(spec) as session:
    block = session.read_block(samples_per_channel=1000)
    print(block.data.shape)
```

Continuous capture iterates as a normal `for` loop:

```python
with Daq.open_device(spec) as session, record(session, chunk_size=1000) as (stream, summary):
    for block in stream:
        process(block)
        if summary.blocks_emitted >= 60:
            break
```

`record_polled` works the same way:

```python
from nidaqlib.sync import record_polled

with Daq.open_device(spec) as session, record_polled(session, rate_hz=2.0) as (stream, _summary):
    for reading in stream:
        store(reading)
```

## What the sync facade does (and doesn't) buy you

The sync facade hides `await`. It does **not**:

- Run multiple sync sessions on the same portal — each `Daq.open_device`
  owns its own portal thread.
- Make sync code faster than async. Calls cross a thread boundary; for
  high-throughput pipelines, prefer the async core.

The async API is canonical. When in doubt, use it.
