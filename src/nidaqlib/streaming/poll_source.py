""":class:`PollSource` Protocol and :class:`PollSourceAdapter` helper.

Cross-library symmetry: every sibling library (``alicatlib``,
``sartoriuslib``, ``watlowlib``) exports a ``PollSourceAdapter`` that
wraps its primary resource into a uniform ``poll(names)`` interface.
nidaqlib's variant wraps a polled :class:`~nidaqlib.tasks.session.DaqSession`
— a single multi-channel task — so the returned mapping has exactly one
entry keyed by the task name.

Block-mode (hardware-clocked) has no poll-source analog — that's the
genuine domain divergence. :func:`record` and :func:`record_polled`
remain separate entry points; the adapter only applies to the polled
side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from nidaqlib.manager import DeviceResult
    from nidaqlib.tasks.models import DaqReading
    from nidaqlib.tasks.session import DaqSession


__all__ = ["PollSource", "PollSourceAdapter"]


@runtime_checkable
class PollSource(Protocol):
    """Anything that yields per-name :class:`DeviceResult[DaqReading]` per call.

    Same name across all four sibling libraries. The recorder layer's
    :func:`record_polled` accepts any :class:`PollSource` instance,
    decoupling the polled producer from the concrete session/manager
    types.
    """

    async def poll(
        self,
        names: Iterable[str] | None = None,
    ) -> Mapping[str, DeviceResult[DaqReading]]:
        """Read once across the named resources (or all, when ``names`` is ``None``)."""
        ...


class PollSourceAdapter:
    """Wrap a polled :class:`DaqSession` as a :class:`PollSource`.

    Multi-channel by design: one DAQ task covers many channels, so the
    returned mapping has exactly one entry — keyed by the task name —
    carrying a multi-channel :class:`DaqReading`. Individual channels stay
    inside ``reading.values``.

    Example::

        adapter = PollSourceAdapter(session)
        async with record_polled(adapter, rate_hz=2.0) as recording:
            async for results in recording.stream:
                reading = results[session.spec.name].value
                ...

    Args:
        session: A started :class:`DaqSession` whose timing is ``None`` or
            :attr:`AcquisitionMode.ON_DEMAND`. The same constraint as
            :meth:`DaqSession.poll`.
    """

    __slots__ = ("_session",)

    def __init__(self, session: DaqSession) -> None:
        self._session = session

    @property
    def name(self) -> str:
        """Mapping key the adapter will use for emitted results."""
        return self._session.spec.name

    async def poll(
        self,
        names: Iterable[str] | None = None,
    ) -> Mapping[str, DeviceResult[DaqReading]]:
        """Read one :class:`DaqReading` and wrap it as ``{name: DeviceResult.success(reading)}``.

        ``names`` is accepted for Protocol uniformity. When provided, the
        adapter only emits a row if the session's name appears in
        ``names`` — otherwise the returned mapping is empty.
        """
        key = self._session.spec.name
        if names is not None and key not in set(names):
            return {}
        from nidaqlib.errors import NIDaqError  # noqa: PLC0415 — late to dodge cycles
        from nidaqlib.manager import DeviceResult  # noqa: PLC0415

        try:
            reading = await self._session.poll()
        except NIDaqError as exc:
            return {key: DeviceResult.failure(exc)}
        return {key: DeviceResult.success(reading)}
