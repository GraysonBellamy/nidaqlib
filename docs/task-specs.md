---
description: Define NI-DAQmx acquisitions declaratively with nidaqlib TaskSpec — channels, timing, triggers, and validated configuration before the hardware bind.
---

# Task specs

`TaskSpec` is the declarative configuration object for one NI task. See
[design doc §8.1](design.md).

## Shape

```python
from nidaqlib import AnalogInputVoltage, AcquisitionMode, TaskSpec, Timing

spec = TaskSpec(
    name="fast_ai",
    channels=[
        AnalogInputVoltage(
            physical_channel="Dev1/ai0",
            name="pressure",
            unit="V",
            min_val=-5.0,
            max_val=5.0,
        ),
    ],
    timing=Timing(
        rate_hz=10_000.0,
        mode=AcquisitionMode.CONTINUOUS,
        samples_per_channel=10_000,
    ),
    metadata={"run_id": "2026-04-29-a"},
)
```

Fields:

- `name` labels the NI task and emitted `DaqReading` / `DaqBlock` rows.
- `channels` is one or more `ChannelSpec` instances. Display names must
  be unique within the task.
- `timing=None` means software-polled/on-demand; `Timing(...)` configures
  an NI sample clock.
- `trigger` accepts a `TriggerSpec` such as `DigitalEdgeStartTrigger` or
  `DigitalEdgeReferenceTrigger`.
- `logging` accepts `TdmsLogging` for NI driver-side TDMS logging.
- `metadata` is scalar, free-form run context propagated into emitted
  records.

`TaskSpec` and channel specs are frozen dataclasses. Construction
validates cheap invariants before anything reaches NI: non-empty task
names, at least one channel, duplicate display names, invalid ranges, and
malformed trigger/channel payloads.

## Serialization

Every task spec round-trips through JSON-friendly dictionaries:

```python
payload = spec.to_dict()
restored = TaskSpec.from_dict(payload)
assert restored == spec
```

Channels and triggers carry a `kind` discriminator so subclasses survive
round-trip serialization. `Timing`, `TriggerSpec`, `TdmsLogging`, and
`RunMetadata` use the same pattern.

## Opening

`open_device` is a plain async factory. Await it to get a configured
`DaqSession`; use the session as the async context manager:

```python
from nidaqlib import open_device

async with await open_device(spec) as session:
    block = await session.read_block(samples_per_channel=1000)
```

Pass `autostart=False` when a recorder needs to register callbacks before
NI starts the task. Pass `confirm_start=True` for counter-output tasks
that actuate on start.
