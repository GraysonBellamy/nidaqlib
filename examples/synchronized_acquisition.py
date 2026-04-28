"""Two AI tasks sharing a master start-trigger via DaqManager.

Run this end-to-end against real hardware (set ``NIDAQLIB_TEST_AI_CHANNEL``
to your AI channel — e.g. ``Dev1/ai0`` — and a second device for the
slave). It exercises :meth:`DaqManager.start_synchronized`: the slave arms
first against the master's ``StartTrigger``, then the master fires and both
tasks acquire from the same start edge.
"""

from __future__ import annotations

import asyncio
import os

from nidaqlib import (
    AnalogInputVoltage,
    DaqManager,
    DigitalEdgeStartTrigger,
    Edge,
    RunMetadata,
    TaskSpec,
    Timing,
    write_sidecar,
)


async def main() -> None:
    master_channel = os.environ.get("NIDAQLIB_TEST_AI_CHANNEL", "Dev1/ai0")
    slave_channel = os.environ.get("NIDAQLIB_TEST_AI_CHANNEL_SLAVE", "Dev2/ai0")
    rate_hz = 10_000.0

    master = TaskSpec(
        name="master",
        channels=[AnalogInputVoltage(physical_channel=master_channel)],
        timing=Timing(rate_hz=rate_hz),
    )
    slave = TaskSpec(
        name="slave",
        channels=[AnalogInputVoltage(physical_channel=slave_channel)],
        timing=Timing(rate_hz=rate_hz),
        # Slave waits on the master's StartTrigger terminal — once the
        # master arms its clock, the slave fires its first sample on the
        # same edge.
        trigger=DigitalEdgeStartTrigger(source="/Dev1/ai/StartTrigger", edge=Edge.RISING),
    )

    async with DaqManager() as mgr:
        await mgr.add("master", master)
        await mgr.add("slave", slave)

        results = await mgr.start_synchronized("master", ["slave"])
        for name, result in results.items():
            print(f"{name}: ok={result.ok}")

        # Read one block from each task; in a real run you would use
        # ``record(...)`` for hardware-clocked streaming.
        blocks = await mgr.read_block(samples_per_channel=1024)
        for name, result in blocks.items():
            if result.ok and result.value is not None:
                print(f"{name}: {result.value.data.shape} samples")

    # Persist the run-level provenance alongside whatever output file the
    # caller writes (TDMS, Parquet, ...). The sidecar is purely
    # declarative — it does not touch the acquisition path.
    metadata = RunMetadata.for_run(
        "synchronized-demo",
        task_specs={"master": master, "slave": slave},
        user_metadata={"experiment": "synchronized demo"},
    )
    sidecar = write_sidecar("acquisition.tdms", metadata)
    print(f"Wrote sidecar: {sidecar}")


if __name__ == "__main__":
    asyncio.run(main())
