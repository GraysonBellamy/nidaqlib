"""Counter channel-spec tests.

Round-trip every concrete counter spec through ``to_dict`` /
``from_dict`` (the registry path on :class:`ChannelSpec`), and confirm
the fake backend records each one through ``open_task``. The real
``NidaqmxBackend`` dispatch table is exercised by the hardware-gated
integration tests; the unit tests verify the registry plumbing.
"""

from __future__ import annotations

import pytest

from nidaqlib import (
    ChannelSpec,
    CounterEdgeCountInput,
    CounterFrequencyInput,
    CounterPeriodInput,
    CounterPulseFrequency,
    CounterPulseTicks,
    CounterPulseTime,
    Edge,
    NIDaqValidationError,
    TaskSpec,
    open_task,
)
from nidaqlib.backend import FakeDaqBackend


def test_counter_frequency_round_trip() -> None:
    spec = CounterFrequencyInput(
        physical_channel="Dev1/ctr0",
        min_val=1.0,
        max_val=1_000_000.0,
        edge=Edge.FALLING,
    )
    restored = ChannelSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_counter_period_round_trip() -> None:
    spec = CounterPeriodInput(
        physical_channel="Dev1/ctr0",
        min_val=1e-6,
        max_val=1e-1,
        edge=Edge.RISING,
    )
    restored = ChannelSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_counter_edge_count_round_trip() -> None:
    spec = CounterEdgeCountInput(
        physical_channel="Dev1/ctr0",
        edge=Edge.FALLING,
        initial_count=42,
        count_up=False,
    )
    restored = ChannelSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_counter_pulse_frequency_round_trip() -> None:
    spec = CounterPulseFrequency(
        physical_channel="Dev1/ctr1",
        frequency=1000.0,
        duty_cycle=0.25,
        initial_delay=0.001,
        idle_high=True,
    )
    restored = ChannelSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_counter_pulse_time_round_trip() -> None:
    spec = CounterPulseTime(
        physical_channel="Dev1/ctr1",
        high_time=0.001,
        low_time=0.001,
    )
    restored = ChannelSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_counter_pulse_ticks_round_trip() -> None:
    spec = CounterPulseTicks(
        physical_channel="Dev1/ctr1",
        source_terminal="/Dev1/20MHzTimebase",
        high_ticks=200,
        low_ticks=200,
    )
    restored = ChannelSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_counter_input_rejects_bad_edge() -> None:
    payload = CounterFrequencyInput(
        physical_channel="Dev1/ctr0", min_val=1.0, max_val=100.0
    ).to_dict()
    payload["edge"] = "sideways"
    with pytest.raises(NIDaqValidationError):
        ChannelSpec.from_dict(payload)


def test_counter_pulse_default_requires_confirm() -> None:
    spec = CounterPulseFrequency(physical_channel="Dev1/ctr0", frequency=1000.0)
    assert spec.requires_confirm is True


@pytest.mark.anyio
async def test_counter_input_addable_via_fake_backend() -> None:
    backend = FakeDaqBackend(read_block_default_shape=(1, 10))
    spec = TaskSpec(
        name="ctr-task",
        channels=[
            CounterFrequencyInput(physical_channel="Dev1/ctr0", min_val=1.0, max_val=1000.0),
        ],
    )
    async with open_task(spec, backend=backend):
        ops = [op.op for op in backend.operations]
    assert ops.count("add_channel") == 1


@pytest.mark.anyio
async def test_counter_output_start_requires_confirm() -> None:
    spec = TaskSpec(
        name="co-task",
        channels=[CounterPulseFrequency(physical_channel="Dev1/ctr0", frequency=1000.0)],
    )
    backend = FakeDaqBackend()
    with pytest.raises(NIDaqValidationError, match="confirm=True"):
        async with open_task(spec, backend=backend):
            pass

    async with open_task(spec, backend=backend, confirm_start=True):
        pass
