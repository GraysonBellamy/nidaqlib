---
description: Safety guarantees in nidaqlib for DAQ outputs that drive heaters, valves, and igniters — deterministic teardown and hardware release on errors.
---

# Safety

DAQ outputs can drive heaters, valves, igniters, regulators, and other
real-world actuators. `nidaqlib` treats them with the same seriousness as
Alicat setpoints or Sartorius state-changing operations: writes are
gated, validated, and never silently coerced. See [design doc §17](design.md).

## Tiers

`nidaqlib` recognises four operational tiers. Each sits behind a
different gate and a different test marker:

| Tier | What it covers | How to opt in | Test marker |
|---|---|---|---|
| **Read-only** | `read_block`, `poll`, `record`, `record_polled` against AI / DI channels. | Default. | `hardware` |
| **Stateful** | Task-state changes (start/stop, configure new task) without writing. | Default — once you have hardware. | `hardware_stateful` |
| **Output** | AO / DO writes and counter-output starts — anything that leaves the device pin energised. | Per-call `confirm=True` plus `safe_min` / `safe_max` clamp where applicable. | `hardware_output` |
| **Destructive** | Calibration, factory ops, anything that can permanently alter the device. | Not implemented in v0.2; reserved. | `hardware_destructive` |

## How the gate works

`DaqSession.write(values, *, confirm=False)` performs three checks in
order, **before** any I/O:

1. **Shape check.** Keys of `values` must exactly match the display names
   of the task's output channels. Unknown or missing keys raise
   `NIDaqValidationError`.
2. **Safe-range check.** For each
   `AnalogOutputVoltage` channel with `safe_min` / `safe_max` set, the
   provided value must lie inside the resolved clamp window
   (`safe_min` falls back to `min_val`, `safe_max` to `max_val`).
   Out-of-range values raise `NIDaqValidationError`. **The library never
   silently clamps.**
3. **Confirmation check.** If any target channel has
   `requires_confirm=True`, the call raises `NIDaqValidationError`
   unless `confirm=True` is passed explicitly.

Only after all three checks pass does the call dispatch to the backend.

Counter-output pulse trains (`CounterPulseFrequency`, `CounterPulseTime`,
`CounterPulseTicks`) actuate on task start, not through `write()`. For
those tasks, pass `confirm_start=True` to `open_device(...)` or call
`session.start(confirm=True)` when restarting an already-open session.

## Defaults are conservative

| Channel | `requires_confirm` default |
|---|:-:|
| `AnalogOutputVoltage` | `True` |
| `DigitalOutput` | `True` |
| `CounterPulseFrequency` / `CounterPulseTime` / `CounterPulseTicks` | `True` |

Override per channel only when you have a reason — for example, a
non-actuating digital indicator line:

```python
from nidaqlib import DigitalOutput

DigitalOutput(
    physical_channel="Dev1/port0/line7",
    name="status_led",
    requires_confirm=False,
)
```

## Safe-range example

```python
from nidaqlib import AnalogOutputVoltage, TaskSpec, open_device

spec = TaskSpec(
    name="heater",
    channels=[
        AnalogOutputVoltage(
            physical_channel="Dev1/ao0",
            name="heater_command",
            min_val=0.0,
            max_val=10.0,
            safe_min=0.0,
            safe_max=5.0,        # never command above 5 V
            requires_confirm=True,
        ),
    ],
)

async with await open_device(spec) as session:
    await session.write({"heater_command": 4.5}, confirm=True)
    # await session.write({"heater_command": 7.0}, confirm=True)
    # ↑ raises NIDaqValidationError — outside [0.0, 5.0]
```

## What the gate does NOT cover

- **The escape hatch.** `session.raw_task` returns the underlying
  `nidaqmx.Task` and intentionally bypasses the gate. If you reach for
  it, you own the safety story end-to-end. Document why.
- **Setpoints applied outside `nidaqlib`.** A SCADA system, MAX,
  another process — these can all drive the same physical lines. The
  gate is a code-level check on this library's call sites.
- **Hardware interlocks.** Always pair output gating with a
  hardware-level safety system (relay coil cut-off, fuse, watchdog).
  `requires_confirm=True` is a software flag on a software API.

## Recommended pattern

Confirm at the session level, once, near the top of a script — not
sprinkled across the call graph. That makes the operator-intent
audit-trail visible:

```python
async with await open_device(spec) as session:
    if not args.dry_run:
        await session.write({"heater_command": 4.5}, confirm=True)
    else:
        log.info("dry-run: would have written %s", values)
```

## See also

- [design doc §17](design.md) — full safety model.
- [docs/testing.md](testing.md) — running the `hardware_output`
  test tier locally.
