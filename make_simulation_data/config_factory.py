from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, List
import random
import uuid6

from scheduler import RunConfig

RangeFactoryType = Callable[[random.Random, int], list[tuple[int, int, int]]]





def build_run_configs(
    num_configs: int,
    seed: int,
    base_config: RunConfig,
    ranges_factory: RangeFactoryType,
) -> List[RunConfig]:
    """
    RunConfig を自動生成する。

    引数:
        num_configs:
            作成する RunConfig の個数
        seed:
            乱数シード
        base_config:
            run_id と ranges 以外の値を持つ雛形
        ranges_factory:
            (rng, index) -> ranges を返す関数

    返り値:
        List[RunConfig]
    """
    rng = random.Random(seed)
    configs: List[RunConfig] = []

    for index in range(num_configs):
        run_uuid = uuid6.uuid7()
        run_id = f"{run_uuid}_{index:04d}"

        ranges = ranges_factory(rng, index)

        config = replace(
            base_config,
            run_id=run_id,
            ranges=ranges,
        )
        configs.append(config)

    return configs