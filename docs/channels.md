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
from nidaqlib import AnalogInputVoltage, TerminalConfiguration

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
- `adc_timing_mode` selects the per-channel ADC timing mode on
  delta-sigma hardware (see [ADC timing mode](#adc-timing-mode) below).
- `auto_zero_mode` selects the per-channel auto-zero behaviour on
  modules that support it (see [Auto-zero mode](#auto-zero-mode) below).

## `ThermocoupleInput`

```python
from nidaqlib import (
    CJCSource,
    TemperatureUnits,
    ThermocoupleInput,
    ThermocoupleType,
)

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

NI driver constants (`ThermocoupleType`, `CJCSource`, `TemperatureUnits`,
`TerminalConfiguration`, `ADCTimingMode`, `AutoZeroType`, `LoggingMode`,
`LoggingOperation`) are re-exported from `nidaqlib` and
`nidaqlib.constants`. They are the same enum members exposed by
`nidaqmx.constants` — `nidaqlib` does not re-shape them.

## AI channel attributes (`AnalogInputBase`)

`AnalogInputVoltage` and `ThermocoupleInput` both inherit from
`AnalogInputBase`, which carries the per-channel knobs NI exposes only
as channel properties on the object returned by `add_ai_*_chan(...)` —
not as kwargs. `nidaqlib` writes each one for you after the channel is
added. Unsupported attributes (e.g. ADC timing on a 9205) surface as
`NIDaqBackendError` carrying NI's error code at set time.

## ADC timing mode

`adc_timing_mode` (with `adc_custom_timing_mode` for the `CUSTOM` case)
trades conversion rate for resolution and configures
line-frequency rejection on delta-sigma modules (NI 9213/9214 in the
thermocouple line, 9239 / 4300-series for voltage).

```python
from nidaqlib import ADCTimingMode, ThermocoupleInput, ThermocoupleType

tc = ThermocoupleInput(
    physical_channel="cDAQ1Mod1/ai0",
    thermocouple_type=ThermocoupleType.K,
    min_val=0.0,
    max_val=300.0,
    adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,   # 24-bit on a 9213
)
```

Available modes:

| Mode | When to pick it |
|---|---|
| `AUTOMATIC` | Default. NI picks based on the configured sample rate. |
| `HIGH_RESOLUTION` | Maximum resolution and noise rejection; lowest conversion rate. Right for slow / high-precision thermocouple or strain reads. |
| `HIGH_SPEED` | Faster conversions, lower resolution. Right when throughput matters more than precision. |
| `BEST_50_HZ_REJECTION` | Filter response tuned to suppress 50 Hz mains hum (EU). |
| `BEST_60_HZ_REJECTION` | Filter response tuned to suppress 60 Hz mains hum (US). |
| `CUSTOM` | Use a device-specific timing mode via `adc_custom_timing_mode` (an integer code). |

Setting `adc_custom_timing_mode` without
`adc_timing_mode=ADCTimingMode.CUSTOM` is rejected at construction time.

## Auto-zero mode

`auto_zero_mode` selects whether the channel performs an auto-zero
calibration to remove DC offset bias. NI exposes this via
`ai_auto_zero_mode`; common on delta-sigma thermocouple modules
(NI 9213/9214) and some voltage modules.

```python
from nidaqlib import AutoZeroType, ThermocoupleInput, ThermocoupleType

tc = ThermocoupleInput(
    physical_channel="cDAQ1Mod1/ai0",
    thermocouple_type=ThermocoupleType.K,
    min_val=0.0,
    max_val=300.0,
    auto_zero_mode=AutoZeroType.ONCE,    # auto-zero at acquisition start
)
```

Available modes:

| Mode | When to pick it |
|---|---|
| `NONE` | Default. No auto-zero. Right when the source is well-behaved or the offset doesn't matter for your measurement. |
| `ONCE` | Auto-zero once when the task starts. The most common useful setting — removes startup-time DC offset without slowing acquisition. |
| `EVERY_SAMPLE` | Auto-zero every conversion. Best DC-offset rejection at the cost of throughput. Right for very-low-drift / very-low-rate work. |

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
