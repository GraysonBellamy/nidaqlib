# Channels

A `ChannelSpec` is a frozen, kw-only dataclass describing one NI physical
channel and the application-side metadata sinks need (display name, unit,
free-form metadata). The core input channel kinds are:

- `AnalogInputVoltage` — `task.ai_channels.add_ai_voltage_chan`.
- `ThermocoupleInput` — `task.ai_channels.add_ai_thrmcpl_chan`.

Output, digital, and counter channel specs are also available:

| Spec | NI call | Notes |
|---|---|---|
| `AnalogOutputVoltage` | `task.ao_channels.add_ao_voltage_chan` | Write through `DaqSession.write`; `requires_confirm=True` by default and optional `safe_min` / `safe_max` are validated before I/O. |
| `DigitalInput` | `task.di_channels.add_di_chan` | Read-only line or port. `line_grouping_per_line=True` by default. |
| `DigitalOutput` | `task.do_channels.add_do_chan` | Write through `DaqSession.write`; `requires_confirm=True` by default. |
| `CounterFrequencyInput` | `task.ci_channels.add_ci_freq_chan` | Frequency measurement in Hz. |
| `CounterPeriodInput` | `task.ci_channels.add_ci_period_chan` | Period measurement in seconds. |
| `CounterEdgeCountInput` | `task.ci_channels.add_ci_count_edges_chan` | Edge counting / totalising. |
| `CounterPulseFrequency` | `task.co_channels.add_co_pulse_chan_freq` | Pulse train by frequency and duty cycle; actuates on start. |
| `CounterPulseTime` | `task.co_channels.add_co_pulse_chan_time` | Pulse train by high/low seconds; actuates on start. |
| `CounterPulseTicks` | `task.co_channels.add_co_pulse_chan_ticks` | Pulse train by source-clock ticks; actuates on start. |

## `AnalogInputVoltage`

```python
from nidaqlib import AnalogInputVoltage
from nidaqmx.constants import TerminalConfiguration

ch = AnalogInputVoltage(
    physical_channel="Dev1/ai0",
    name="pressure",          # display name; defaults to physical_channel
    unit="V",                 # free-form; sinks use it for column headers
    min_val=-5.0,
    max_val=5.0,
    terminal_config=TerminalConfiguration.RSE,
)
```

When to pick which:

- `min_val` / `max_val` set the expected input range. NI uses this to
  pick the lowest gain that won't clip — narrower is better when you
  know the signal stays small.
- `terminal_config` defaults to whatever the device prefers. Set it
  explicitly when you need RSE / NRSE / DIFF / PSEUDO_DIFF and the
  device default is wrong.
- `custom_scale_name` references a pre-configured scale in NI MAX. With
  it set, `min_val`/`max_val` are scaled engineering units, not volts.

## `ThermocoupleInput`

```python
from nidaqlib import ThermocoupleInput
from nidaqmx.constants import CJCSource, TemperatureUnits, ThermocoupleType

oven = ThermocoupleInput(
    physical_channel="Dev1/ai2",
    name="oven_top",
    unit="degC",
    thermocouple_type=ThermocoupleType.K,
    min_val=0.0,
    max_val=200.0,
    cjc_source=CJCSource.BUILT_IN,
    units=TemperatureUnits.DEG_C,   # default
)
```

Re-export note: `ThermocoupleType`, `CJCSource`, and `TemperatureUnits`
come from `nidaqmx.constants`. Import them from there directly — they
are NI's constants and `nidaqlib` does not re-shape them.

## When to use voltage vs. thermocouple

- **Voltage** is the right choice for almost any signal you condition
  externally (pressure transducer with 0–5 V output, generic voltage
  reading, current sensor scaled to volts). The conversion to
  engineering units happens off-device — either via a NI custom scale
  or in your application code.
- **Thermocouple** is the right choice when you wire a TC junction
  directly to the SCB / device terminals. NI handles the cold-junction
  compensation and linearisation; you get temperature out, not voltage.

If you are conditioning the TC externally (e.g. with a 4-20 mA
transmitter that already converts to engineering units), use
`AnalogInputVoltage` and apply your own scale.

## Outputs and confirmation

Output-capable specs default to `requires_confirm=True` because they can
drive real hardware. Analog outputs also validate a safe range:

```python
from nidaqlib import AnalogOutputVoltage, TaskSpec, open_device

spec = TaskSpec(
    name="heater_command",
    channels=[
        AnalogOutputVoltage(
            physical_channel="Dev1/ao0",
            name="heater_v",
            min_val=0.0,
            max_val=10.0,
            safe_max=5.0,
        ),
    ],
)

async with await open_device(spec) as session:
    await session.write({"heater_v": 3.5}, confirm=True)
```

Counter-output pulse trains actuate when the task starts, so pass
`confirm_start=True` to `open_device(...)` or call
`session.start(confirm=True)` when starting manually.

## Round-trip serialisation

Every `ChannelSpec` carries a `kind: ClassVar[str]` discriminator and
implements `to_dict()` / `from_dict()` so configurations round-trip
through JSON or TOML without losing the subclass identity:

```python
restored = ChannelSpec.from_dict(spec.to_dict())
assert restored == spec
```

Enum-typed fields (`thermocouple_type`, `units`, `cjc_source`) are
serialised by their integer `.value` and reconstructed on the other
side.
