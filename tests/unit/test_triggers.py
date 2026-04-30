"""Trigger-spec tests (design doc §8.1).

Covers:

- Round-trip ``to_dict`` / ``from_dict`` for every concrete
  :class:`TriggerSpec` subclass.
- Registry-dispatch failure modes (unknown kind, missing kind, kind
  mismatch on a concrete subclass).
- Validation: zero / negative pretrigger samples are rejected.
- Backend ordering: triggers are configured *after* timing in
  :meth:`DaqSession._configure_sync`.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogEdgeStartTrigger,
    AnalogInputVoltage,
    AnalogTriggerSlope,
    DigitalEdgeReferenceTrigger,
    DigitalEdgeStartTrigger,
    Edge,
    NIDaqValidationError,
    TaskSpec,
    Timing,
    TriggerSpec,
    open_device,
)
from nidaqlib.backend import FakeDaqBackend


def _spec_with_trigger(trigger: TriggerSpec) -> TaskSpec:
    return TaskSpec(
        name="trigger-task",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.FINITE, samples_per_channel=100),
        trigger=trigger,
    )


def test_digital_edge_start_round_trip() -> None:
    trig = DigitalEdgeStartTrigger(source="/Dev1/PFI0", edge=Edge.FALLING)
    payload = trig.to_dict()
    restored = TriggerSpec.from_dict(payload)
    assert restored == trig


def test_analog_edge_start_round_trip() -> None:
    trig = AnalogEdgeStartTrigger(source="/Dev1/ai0", level=2.5, slope=AnalogTriggerSlope.FALLING)
    restored = TriggerSpec.from_dict(trig.to_dict())
    assert restored == trig


def test_digital_edge_reference_round_trip() -> None:
    trig = DigitalEdgeReferenceTrigger(
        source="/Dev1/PFI1", pretrigger_samples=128, edge=Edge.RISING
    )
    restored = TriggerSpec.from_dict(trig.to_dict())
    assert restored == trig


def test_reference_trigger_rejects_zero_pretrigger() -> None:
    with pytest.raises(NIDaqValidationError):
        DigitalEdgeReferenceTrigger(source="/Dev1/PFI0", pretrigger_samples=0)


def test_reference_trigger_rejects_negative_pretrigger() -> None:
    with pytest.raises(NIDaqValidationError):
        DigitalEdgeReferenceTrigger(source="/Dev1/PFI0", pretrigger_samples=-1)


def test_unknown_kind_rejected() -> None:
    with pytest.raises(NIDaqValidationError):
        TriggerSpec.from_dict({"kind": "no_such_trigger", "source": "/Dev1/PFI0"})


def test_missing_kind_rejected() -> None:
    with pytest.raises(NIDaqValidationError):
        TriggerSpec.from_dict({"source": "/Dev1/PFI0"})


def test_concrete_class_kind_mismatch() -> None:
    with pytest.raises(NIDaqValidationError):
        DigitalEdgeStartTrigger.from_dict({"kind": "analog_edge_start", "source": "/Dev1/ai0"})


def test_taskspec_round_trips_trigger() -> None:
    spec = _spec_with_trigger(DigitalEdgeStartTrigger(source="/Dev1/PFI0"))
    restored = TaskSpec.from_dict(spec.to_dict())
    assert isinstance(restored.trigger, DigitalEdgeStartTrigger)
    assert restored.trigger.source == "/Dev1/PFI0"


def test_taskspec_round_trip_with_no_trigger() -> None:
    spec = TaskSpec(
        name="no-trigger",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
        timing=Timing(rate_hz=1000.0),
    )
    restored = TaskSpec.from_dict(spec.to_dict())
    assert restored.trigger is None


def test_taskspec_rejects_non_mapping_trigger() -> None:
    payload = TaskSpec(
        name="t",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
    ).to_dict()
    payload["trigger"] = "not-a-mapping"
    with pytest.raises(NIDaqValidationError):
        TaskSpec.from_dict(payload)


@pytest.mark.anyio
async def test_trigger_configured_after_timing() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    spec = _spec_with_trigger(DigitalEdgeStartTrigger(source="/Dev1/PFI0"))
    async with await open_device(spec, backend=backend):
        ops = [op.op for op in backend.operations]
    timing_idx = ops.index("configure_timing")
    trigger_idx = ops.index("configure_trigger")
    start_idx = ops.index("start_task")
    assert timing_idx < trigger_idx < start_idx


@pytest.mark.anyio
async def test_trigger_recorded_on_fake_task() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    trig = DigitalEdgeStartTrigger(source="/Dev1/PFI0", edge=Edge.FALLING)
    spec = _spec_with_trigger(trig)
    async with await open_device(spec, backend=backend) as session:
        del session  # we only care about the side-effect on the fake task
        # The fake task survives until the context exits — find it now.
        fake_task = next(iter(backend._tasks.values()))  # pyright: ignore[reportPrivateUsage]
        assert fake_task.trigger == trig
