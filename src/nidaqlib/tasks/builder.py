"""Fluent :class:`TaskSpec` builder.

A small convenience layer on top of the dataclass — useful for notebook /
example call sites that incrementally accumulate channels. The dataclass
constructor remains the canonical entry point for typed, validated specs.

See design doc Appendix B.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from nidaqlib.tasks.spec import TaskSpec, Timing

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nidaqlib.channels.base import ChannelSpec


class TaskBuilder:
    """Fluent builder for :class:`TaskSpec`.

    Example:
        >>> spec = (
        ...     TaskBuilder("ai_demo")
        ...     .add_channel(AnalogInputVoltage(physical_channel="Dev1/ai0"))
        ...     .with_timing(Timing(rate_hz=1000.0))
        ...     .build()
        ... )
    """

    def __init__(self, name: str) -> None:
        """Create a builder for a task named ``name``.

        Args:
            name: Task name. Will become :attr:`TaskSpec.name`.
        """
        self._name = name
        self._channels: list[ChannelSpec] = []
        self._timing: Timing | None = None
        self._metadata: dict[str, str | int | float | bool] = {}

    def add_channel(self, channel: ChannelSpec) -> Self:
        """Append a channel to the task. Returns self for chaining."""
        self._channels.append(channel)
        return self

    def with_timing(self, timing: Timing) -> Self:
        """Set the task's :class:`Timing`. Returns self for chaining."""
        self._timing = timing
        return self

    def with_metadata(self, metadata: Mapping[str, str | int | float | bool]) -> Self:
        """Merge ``metadata`` into the builder's metadata dict.

        Returns self for chaining. Later calls overwrite earlier keys.
        """
        self._metadata.update(metadata)
        return self

    def build(self) -> TaskSpec:
        """Construct the immutable :class:`TaskSpec`."""
        return TaskSpec(
            name=self._name,
            channels=tuple(self._channels),
            timing=self._timing,
            metadata=dict(self._metadata),
        )


__all__ = ["TaskBuilder"]
