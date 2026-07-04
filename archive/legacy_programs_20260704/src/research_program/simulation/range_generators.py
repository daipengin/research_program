from __future__ import annotations

import random
from typing import List, Tuple

RangeType = List[Tuple[int, int, int]]


def sample_unique_points(
    rng: random.Random,
    n: int,
    step: int,
    k: int,
) -> List[int]:
    """
    0, step, 2*step, ..., n*step の中から
    重複なしで k 個サンプリングする。
    """
    population = range(0, n * step + 1, step)
    return rng.sample(population, k)


def generate_ranges_same_duration_from_unique_starts(
    rng: random.Random,
    n: int,
    step: int,
    k: int,
    duration: int,
    start_device_id: int = 0,
) -> RangeType:
    """
    重複なしで開始時刻をサンプリングし，
    各開始時刻に対して同じ duration を持つ ranges を作る。

    返り値:
        [(start, end, device_id), ...]
    """
    starts = sample_unique_points(rng=rng, n=n, step=step, k=k)
    starts.sort()

    ranges: RangeType = []
    for i, start_time in enumerate(starts):
        end_time = start_time + duration
        device_id = start_device_id + i
        ranges.append((start_time, end_time, device_id))

    
    #ranges = [(0,duration,0),(20,duration,1),(40,duration,2),(60,duration,3),(80,duration,4),(7500-480,duration,5),(7500-460,duration,6),(7500-440,duration,7),(7500-420,duration,8),(7500-400,duration,9),(15000-480,duration,10),(15000-460,duration,11),(15000-440,duration,12),(15000-420,duration,13),(15000-400,duration,14),(22500-480,duration,15),(22500-460,duration,16),(22500-440,duration,17),(22500-420,duration,18),(22500-400,duration,19)]


    return ranges


def generate_even_interval_start_times(
    k: int,
    interval: int,
    start_time: int = 0,
) -> List[int]:
    if k < 1:
        raise ValueError("k must be at least 1")
    if interval < 0:
        raise ValueError("interval must be non-negative")
    return [start_time + i * interval for i in range(k)]


def generate_ranges_from_start_times(
    start_times: List[int],
    duration: int,
    start_device_id: int = 0,
) -> RangeType:
    if duration < 1:
        raise ValueError("duration must be at least 1")

    ranges: RangeType = []
    for i, start_time in enumerate(start_times):
        end_time = int(start_time) + duration
        device_id = start_device_id + i
        ranges.append((int(start_time), end_time, device_id))
    return ranges


def parse_start_times_text(text: str) -> List[int]:
    normalized = text.replace("\n", ",").replace(";", ",")
    if not normalized.strip():
        return []
    return [int(part.strip()) for part in normalized.split(",") if part.strip()]
