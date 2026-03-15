"""CSI packet dataclass for WiFi CSI signal processing.

Handles deserialization from binary MQTT payloads (ESP32-S3 HT40 format),
I/Q to amplitude/phase conversion, and JSON serialization for testing.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np

NUM_SUBCARRIERS: int = 114  # HT40 mode

# Binary format: uint64 timestamp | 6B tx_mac | 6B rx_mac | int8 rssi | uint8 floor_id | 114x(int16 I, int16 Q)
# Total: 8 + 6 + 6 + 1 + 1 + 114*4 = 478 bytes
_HEADER_FMT = "<Q6s6sbB"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 22 bytes
_IQ_FMT = f"<{NUM_SUBCARRIERS * 2}h"
_IQ_SIZE = struct.calcsize(_IQ_FMT)  # 456 bytes
PACKET_SIZE = _HEADER_SIZE + _IQ_SIZE  # 478 bytes


def _mac_bytes_to_str(mac: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac)


def _mac_str_to_bytes(mac: str) -> bytes:
    return bytes(int(x, 16) for x in mac.split(":"))


class MalformedPacketError(Exception):
    """Raised when a binary payload cannot be deserialized into a CsiPacket."""


@dataclass(frozen=True)
class CsiPacket:
    """A single CSI measurement from an ESP32-S3 receiver (HT40, 114 subcarriers).

    Attributes:
        timestamp_us: Microsecond timestamp from the ESP32 clock.
        tx_mac: Transmitter MAC address (e.g. "aa:bb:cc:dd:ee:ff").
        rx_mac: Receiver MAC address.
        rssi: Received signal strength indicator (dBm), typically negative.
        floor_id: Floor identifier (0-based).
        iq_pairs: Flat list of 228 int16 values: [I0, Q0, I1, Q1, ...].
    """

    timestamp_us: int
    tx_mac: str
    rx_mac: str
    rssi: int
    floor_id: int
    iq_pairs: list[int] = field(repr=False)

    _NUM_SUBCARRIERS: ClassVar[int] = NUM_SUBCARRIERS

    def __post_init__(self) -> None:
        if len(self.iq_pairs) != self._NUM_SUBCARRIERS * 2:
            raise ValueError(
                f"Expected {self._NUM_SUBCARRIERS * 2} I/Q values, "
                f"got {len(self.iq_pairs)}"
            )

    # ── Binary deserialization ──────────────────────────────────────────

    @classmethod
    def from_bytes(cls, data: bytes) -> CsiPacket:
        """Deserialize a CsiPacket from a binary MQTT payload.

        Raises:
            MalformedPacketError: If the payload size is wrong or struct
                unpacking fails.
        """
        if len(data) != PACKET_SIZE:
            raise MalformedPacketError(
                f"Expected {PACKET_SIZE} bytes, got {len(data)}"
            )
        try:
            header = struct.unpack_from(_HEADER_FMT, data, 0)
            iq_raw = struct.unpack_from(_IQ_FMT, data, _HEADER_SIZE)
        except struct.error as exc:
            raise MalformedPacketError(f"Struct unpack failed: {exc}") from exc

        timestamp_us, tx_mac_b, rx_mac_b, rssi, floor_id = header
        return cls(
            timestamp_us=timestamp_us,
            tx_mac=_mac_bytes_to_str(tx_mac_b),
            rx_mac=_mac_bytes_to_str(rx_mac_b),
            rssi=rssi,
            floor_id=floor_id,
            iq_pairs=list(iq_raw),
        )

    def to_bytes(self) -> bytes:
        """Serialize to the binary MQTT format."""
        header = struct.pack(
            _HEADER_FMT,
            self.timestamp_us,
            _mac_str_to_bytes(self.tx_mac),
            _mac_str_to_bytes(self.rx_mac),
            self.rssi,
            self.floor_id,
        )
        iq = struct.pack(_IQ_FMT, *self.iq_pairs)
        return header + iq

    # ── JSON serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "timestamp_us": self.timestamp_us,
            "tx_mac": self.tx_mac,
            "rx_mac": self.rx_mac,
            "rssi": self.rssi,
            "floor_id": self.floor_id,
            "iq_pairs": self.iq_pairs,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> CsiPacket:
        return cls(
            timestamp_us=d["timestamp_us"],
            tx_mac=d["tx_mac"],
            rx_mac=d["rx_mac"],
            rssi=d["rssi"],
            floor_id=d["floor_id"],
            iq_pairs=d["iq_pairs"],
        )

    @classmethod
    def from_json(cls, s: str) -> CsiPacket:
        return cls.from_dict(json.loads(s))

    # ── I/Q → amplitude & phase ─────────────────────────────────────────

    @property
    def amplitude_array(self) -> np.ndarray:
        """Amplitude per subcarrier: sqrt(I² + Q²). Shape: (114,)."""
        iq = np.array(self.iq_pairs, dtype=np.float64).reshape(-1, 2)
        return np.sqrt(iq[:, 0] ** 2 + iq[:, 1] ** 2)

    @property
    def phase_array(self) -> np.ndarray:
        """Phase per subcarrier: atan2(Q, I) in radians. Shape: (114,)."""
        iq = np.array(self.iq_pairs, dtype=np.float64).reshape(-1, 2)
        return np.arctan2(iq[:, 1], iq[:, 0])

    @property
    def complex_array(self) -> np.ndarray:
        """Complex I/Q per subcarrier: I + jQ. Shape: (114,)."""
        iq = np.array(self.iq_pairs, dtype=np.float64).reshape(-1, 2)
        return iq[:, 0] + 1j * iq[:, 1]
