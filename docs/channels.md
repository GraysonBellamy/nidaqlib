# Channels

A `ChannelSpec` is a frozen, kw-only dataclass describing one NI physical
channel and the application-side metadata sinks need (display name, unit,
free-form metadata). The core input channel kinds are:

- `AnalogInputVoltage` — `task.ai_channels.add_ai_voltage_chan`.
- `ThermocoupleInput` — `task.ai_channels.add_ai_thrmcpl_chan`.

Output, digital, and counter channel specs are also available.

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
