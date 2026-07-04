from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LoRaAirtimeConfig:
    payload_bytes: int = 16
    spreading_factor: int = 7
    bandwidth_hz: int = 125_000
    coding_rate_denominator: int = 5
    preamble_symbols: int = 8
    explicit_header: bool = True
    crc_enabled: bool = True
    low_data_rate_optimize: bool | None = None


def resolve_low_data_rate_optimize(config: LoRaAirtimeConfig) -> bool:
    if config.low_data_rate_optimize is not None:
        return bool(config.low_data_rate_optimize)
    symbol_duration_sec = (2**config.spreading_factor) / float(config.bandwidth_hz)
    return symbol_duration_sec >= 0.016


def calculate_lora_airtime_ms(config: LoRaAirtimeConfig) -> float:
    if config.payload_bytes < 0:
        raise ValueError("payload_bytes must be non-negative")
    if not 6 <= config.spreading_factor <= 12:
        raise ValueError("spreading_factor must be between 6 and 12")
    if config.bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz must be positive")
    if not 5 <= config.coding_rate_denominator <= 8:
        raise ValueError("coding_rate_denominator must be between 5 and 8")
    if config.preamble_symbols < 0:
        raise ValueError("preamble_symbols must be non-negative")

    sf = config.spreading_factor
    bw = float(config.bandwidth_hz)
    cr = config.coding_rate_denominator - 4
    header_disabled = 0 if config.explicit_header else 1
    crc = 1 if config.crc_enabled else 0
    low_data_rate_optimize = 1 if resolve_low_data_rate_optimize(config) else 0

    symbol_duration_sec = (2**sf) / bw
    preamble_duration_sec = (config.preamble_symbols + 4.25) * symbol_duration_sec

    numerator = 8 * config.payload_bytes - 4 * sf + 28 + 16 * crc - 20 * header_disabled
    denominator = 4 * (sf - 2 * low_data_rate_optimize)
    payload_symbols = 8 + max(math.ceil(numerator / denominator) * (cr + 4), 0)
    payload_duration_sec = payload_symbols * symbol_duration_sec

    return (preamble_duration_sec + payload_duration_sec) * 1000.0
