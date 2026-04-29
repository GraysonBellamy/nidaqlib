"""Backend protocol — the seam where ``nidaqlib`` plugs into NI's driver.

The protocol covers task lifecycle, channel configuration, reading, writing,
and trigger setup without exposing the rest of ``nidaqmx-python``.

There is no transport-level seam in DAQ (no bytes on the wire), so the fake
substitution point lives one layer up — at the ``DaqBackend`` Protocol.
Tests inject :class:`~nidaqlib.backend.fake.FakeDaqBackend`. See design doc
§10.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import numpy as np

    from nidaqlib.channels.base import ChannelSpec
    from nidaqlib.system.models import DeviceInfo
    from nidaqlib.tasks.spec import TdmsLogging, Timing
    from nidaqlib.tasks.triggers import TriggerSpec


CallbackHandle = Any
"""Backend-defined receipt for a registered every-N-samples callback.

Real :class:`~nidaqlib.backend.nidaqmx_backend.NidaqmxBackend` returns the
underlying ``nidaqmx`` task object (NI exposes registration as a method on
the task with no separate handle). The fake backend returns its own opaque
handle. Callers treat this as opaque — pass it back to
:meth:`DaqBackend.unregister_every_n_samples`.
"""


class DaqBackend(Protocol):
    """Operations the rest of :mod:`nidaqlib` needs from the NI driver layer.

    Implementations must wrap ``nidaqmx.errors.DaqError`` (and equivalents)
    into :class:`~nidaqlib.errors.NIDaqError` subclasses, preserving the
    original via ``__cause__`` and populating
    :class:`~nidaqlib.errors.ErrorContext`.
    """

    def create_task(self, name: str) -> Any:
        """Create and return an underlying task handle."""
        ...

    def close_task(self, task: Any) -> None:
        """Release the underlying task handle. Idempotent."""
        ...

    def add_channel(self, task: Any, spec: ChannelSpec) -> None:
        """Add a channel described by ``spec`` to ``task``."""
        ...

    def configure_timing(self, task: Any, timing: Timing) -> None:
        """Apply :class:`Timing` to ``task``."""
        ...

    def configure_logging(self, task: Any, logging: TdmsLogging) -> None:
        """Configure driver-side TDMS logging on ``task`` (design doc §14.6).

        Maps to ``task.in_stream.configure_logging(...)`` on the real backend.
        Called once, after channels are added and before
        :meth:`configure_timing`.
        """
        ...

    def configure_trigger(self, task: Any, trigger: TriggerSpec) -> None:
        """Configure a start- or reference-trigger on ``task``.

        Implementations dispatch on the concrete :class:`TriggerSpec`
        subclass:

        - :class:`~nidaqlib.tasks.triggers.DigitalEdgeStartTrigger` →
          ``task.triggers.start_trigger.cfg_dig_edge_start_trig``.
        - :class:`~nidaqlib.tasks.triggers.AnalogEdgeStartTrigger` →
          ``task.triggers.start_trigger.cfg_anlg_edge_start_trig``.
        - :class:`~nidaqlib.tasks.triggers.DigitalEdgeReferenceTrigger` →
          ``task.triggers.reference_trigger.cfg_dig_edge_ref_trig``.

        Called once, **after** :meth:`configure_timing` (NI requires the
        sample clock to be configured before a reference trigger is set).
        """
        ...

    def start_task(self, task: Any) -> None:
        """Start ``task`` (transition to running)."""
        ...

    def stop_task(self, task: Any) -> None:
        """Stop ``task`` (transition to committed)."""
        ...

    def read_block(
        self,
        task: Any,
        samples_per_channel: int,
        timeout: float,
    ) -> np.ndarray:
        """Block until ``samples_per_channel`` samples are available, return them.

        Returns:
            ``np.ndarray`` of shape ``(n_channels, samples_per_channel)``,
            ``dtype=float64`` for analog-input tasks.
        """
        ...

    def write(
        self,
        task: Any,
        values: Mapping[str, float | bool],
        timeout: float,
    ) -> None:
        """Write one sample-per-channel to ``task``.

        Keys of ``values`` are the channel display names declared on the
        spec (``ChannelSpec.display_name`` / NI's
        ``name_to_assign_to_channel``). Implementations dispatch on the
        channel kinds present on the task — AO writes go through
        ``AnalogMultiChannelWriter``; DO writes through
        ``DigitalMultiChannelWriter``. Mixing kinds in one task is a
        configuration error and SHOULD be rejected.

        :meth:`DaqSession.write` performs all safety-gate validation
        (``confirm=True``, ``safe_min`` / ``safe_max``) before this call —
        backends MUST NOT silently clamp or coerce.
        """
        ...

    def register_every_n_samples(
        self,
        task: Any,
        n: int,
        callback: Callable[[int], None],
    ) -> CallbackHandle:
        """Register a buffer-event callback that fires every ``n`` samples.

        The callback runs on a *driver thread* — implementations must not
        forward asyncio / anyio primitives to it. See design doc §11.3.2.

        Args:
            task: Backend task handle from :meth:`create_task`.
            n: Sample-count cadence. Must be > 0.
            callback: Receives ``n`` (the number of samples now available).
                Implementations are responsible for ensuring the callback
                receipt outlives the registration — NI stores raw C function
                pointers and Python GC will silently break the seam.

        Returns:
            An opaque :data:`CallbackHandle` to pass to
            :meth:`unregister_every_n_samples`.
        """
        ...

    def unregister_every_n_samples(self, task: Any, handle: CallbackHandle) -> None:
        """Unregister a previously-registered buffer-event callback.

        After this returns, the backend guarantees no further invocations of
        the callback. MUST be called *after* :meth:`stop_task` on the same
        task — NI rejects unregister on a running task with -200986. See the
        §11.3.2 ordering invariants for the full stop → unregister →
        sentinel → drain sequence.
        """
        ...

    def device_info(self, device: str) -> DeviceInfo | None:
        """Return product / channel info for ``device``, or ``None`` if unknown.

        Used by :class:`~nidaqlib.manager.DaqManager` preflight to detect
        module-level reservation classes (e.g. NI 9211/9212/9213/9214 TC
        modules reserve the whole module per task). Implementations MAY
        return ``None`` when the device is unknown to the backend or when
        no such information is available (e.g. the fake backend).
        """
        ...


__all__ = ["CallbackHandle", "DaqBackend"]
