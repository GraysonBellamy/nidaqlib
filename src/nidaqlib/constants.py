"""NI driver constants re-exported under the :mod:`nidaqlib` namespace.

The wrapper does not re-shape these — they are :mod:`nidaqmx.constants`
enum members, imported here so users have a single coherent public
surface for everything they need to construct typed channel and timing
specs:

```python
from nidaqlib import AnalogInputVoltage
from nidaqlib.constants import ADCTimingMode, AutoZeroType, TerminalConfiguration

ch = AnalogInputVoltage(
    physical_channel="Dev1/ai0",
    terminal_config=TerminalConfiguration.RSE,
    adc_timing_mode=ADCTimingMode.HIGH_RESOLUTION,
    auto_zero_mode=AutoZeroType.ONCE,
)
```

The most-used members are also re-exported at the top level
(``from nidaqlib import ADCTimingMode``). Use whichever import style
fits the call site.
"""

from __future__ import annotations

from nidaqmx.constants import (
    ADCTimingMode,
    AutoZeroType,
    CJCSource,
    LoggingMode,
    LoggingOperation,
    TemperatureUnits,
    TerminalConfiguration,
    ThermocoupleType,
)

__all__ = [
    "ADCTimingMode",
    "AutoZeroType",
    "CJCSource",
    "LoggingMode",
    "LoggingOperation",
    "TemperatureUnits",
    "TerminalConfiguration",
    "ThermocoupleType",
]
