# Timing

`nidaqlib` exposes three timing concepts. They compose: a `Timing` covers
the sample clock, a `TriggerSpec` covers when sampling begins, and the
manager-level `start_synchronized` covers ordering across multiple
tasks.

## Sample-clock timing

`Timing` configures the on-board sample clock. The defaults match NI's
behaviour: continuous, on-board clock, rising edge.

```python
from nidaqlib import AcquisitionMode, Timing

# Hardware-timed, continuous, 50 kHz on the on-board clock.
Timing(rate_hz=50_000.0)

# Finite acquisition: 10 000 samples per channel at 1 kHz.
Timing(rate_hz=1_000.0, mode=AcquisitionMode.FINITE, samples_per_channel=10_000)

# Hardware-timed, but driven by an external sample-clock terminal.
Timing(rate_hz=10_000.0, source="/Dev1/PFI0")
```

Pass `Timing` to a `TaskSpec`'s `timing=` field for hardware-clocked
finite or continuous acquisition. Leaving `timing=None` selects
software-timed, single-sample-per-call behaviour — useful for low-rate
polling with `poll()` or `record_polled()`.

`AcquisitionMode.ON_DEMAND` is accepted as an explicit software-polled
marker and does **not** configure NI sample-clock timing. Prefer
`timing=None` unless you need the marker for serialized specs.

## Triggers

A `TriggerSpec` controls *when* sampling starts (and, for reference
triggers, what counts as the "during the run" window). Three concrete
kinds:

| Kind | When the task starts | Use when |
|---|---|---|
| `DigitalEdgeStartTrigger` | The first edge on a digital terminal (PFI / RTSI / shared trigger line). | One task syncs from an external pulse, or a slave task syncs from a master's `ai/StartTrigger`. |
| `AnalogEdgeStartTrigger` | An analog source channel crosses `level` on the configured slope. | Self-triggering on a measured signal — e.g. a pressure transient. |
| `DigitalEdgeReferenceTrigger` | The whole task is finite. NI captures `pretrigger_samples` *before* the edge plus the remainder after. | Capturing the lead-up to an event. **Finite mode only**. |

```python
from nidaqlib import (
    DigitalEdgeStartTrigger,
    AnalogEdgeStartTrigger,
    AnalogTriggerSlope,
    DigitalEdgeReferenceTrigger,
    Edge,
)

# Wait for a rising digital edge on PFI0 before sampling.
DigitalEdgeStartTrigger(source="/Dev1/PFI0", edge=Edge.RISING)

# Self-trigger when ai0 crosses 2.5 V on a falling slope.
AnalogEdgeStartTrigger(
    source="/Dev1/ai0", level=2.5, slope=AnalogTriggerSlope.FALLING
)

# Finite acquisition, 1024 pretrigger samples then the rest.
DigitalEdgeReferenceTrigger(source="/Dev1/PFI1", pretrigger_samples=1024)
```

NI requires the sample clock to be configured before a trigger. The
wrapper's `DaqSession._configure_sync` enforces that ordering — you do
not need to think about it.

## Multi-task synchronisation

When you need two or more tasks to start from the same clock or trigger, `DaqManager`
offers `start_synchronized`. The pattern is:

1. Configure the **slave** tasks against the master's terminal — either
   share the master's sample clock (set `Timing.source` to e.g.
   `/Dev1/ai/SampleClock`) or set a `DigitalEdgeStartTrigger` on the
   master's `ai/StartTrigger`.
2. Call `mgr.start_synchronized(master, slaves)`. Slaves are armed
   sequentially (each `start_task` returns once the slave is in the
   *armed-and-waiting* state); the master is started last. Once the
   master fires its clock or trigger, every slave wakes immediately.

```python
from nidaqlib import (
    AnalogInputVoltage, DaqManager, DigitalEdgeStartTrigger,
    Edge, TaskSpec, Timing,
)

master = TaskSpec(
    name="master",
    channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
    timing=Timing(rate_hz=10_000.0),
)
slave = TaskSpec(
    name="slave",
    channels=[AnalogInputVoltage(physical_channel="Dev2/ai0")],
    timing=Timing(rate_hz=10_000.0),
    trigger=DigitalEdgeStartTrigger(
        source="/Dev1/ai/StartTrigger", edge=Edge.RISING
    ),
)

async with DaqManager() as mgr:
    await mgr.add("master", master)
    await mgr.add("slave", slave)
    await mgr.start_synchronized("master", ["slave"])
    # ... acquire ...
    # close() unwinds in LIFO order on context exit.
```

If a slave fails to arm, every previously-armed slave is stopped before
the error is raised, and the master is **not** started. Under
`ErrorPolicy.RETURN`, the master's `DeviceResult.error` carries a
`NIDaqTaskStateError` explaining why.

`start_synchronized` is intentionally simpler than `start()` — sequential
rather than parallel arming. The difference matters when arming fails:
the master must not start at all, which is hard to guarantee with
concurrent fan-out.
