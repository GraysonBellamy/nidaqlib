---
description: Test nidaqlib acquisition code without NI hardware using the FakeDaqBackend, recorded fixtures, and deterministic time sources.
---

# Testing

`FakeDaqBackend` supports hardware-free unit tests. See [design doc §21](design.md).

## Hardware test gating

Tests that need real NI hardware are split into four tiers, all skipped by default in CI:

| Marker | Opt-in env var | Description |
|---|---|---|
| `hardware` | `NIDAQLIB_ENABLE_HARDWARE_TESTS=1` | Read-only access to a connected DAQ device. |
| `hardware_stateful` | `NIDAQLIB_ENABLE_STATEFUL_TESTS=1` | Changes task / device state (start/stop/reconfigure). |
| `hardware_output` | `NIDAQLIB_ENABLE_OUTPUT_TESTS=1` | Writes analog / digital / counter output. |
| `hardware_destructive` | `NIDAQLIB_ENABLE_DESTRUCTIVE_TESTS=1` | Calibration or other potentially unsafe operations. |

The integration suite is currently tuned for the thermocouple hardware-day
surface. Configure it with:

```text
NIDAQLIB_TEST_TC_DEVICE=cDAQ1Mod1
NIDAQLIB_TEST_TC_CHANNEL_PRIMARY=cDAQ1Mod1/ai0
NIDAQLIB_TEST_TC_CHANNEL_SECONDARY=cDAQ1Mod1/ai1  # optional
NIDAQLIB_TEST_TC_TYPE=K                           # default K
NIDAQLIB_TEST_TC_RATE_HZ=10                       # default 10
NIDAQLIB_TEST_TC_MIN_DEGC=-50                     # default -50
NIDAQLIB_TEST_TC_MAX_DEGC=200                     # default 200
```

If `NIDAQLIB_TEST_TC_CHANNEL_PRIMARY` is unset, the tests synthesize
`<device>/ai0` from `NIDAQLIB_TEST_TC_DEVICE`. Two-channel tests skip
cleanly when `NIDAQLIB_TEST_TC_CHANNEL_SECONDARY` is unset.
