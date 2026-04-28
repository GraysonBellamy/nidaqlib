"""Backend layer — protocol + real and fake implementations.

See design doc §10.
"""

from __future__ import annotations

from nidaqlib.backend.base import CallbackHandle, DaqBackend
from nidaqlib.backend.fake import FakeDaqBackend
from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend

__all__ = ["CallbackHandle", "DaqBackend", "FakeDaqBackend", "NidaqmxBackend"]
