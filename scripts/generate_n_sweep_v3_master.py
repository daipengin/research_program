from __future__ import annotations

import json
import random
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v3" / "initial_phase_master.json"
SEED = 20_261_990
RUN_COUNT = 1_000
DEVICE_COUNT = 50
RANGE_MS = 5_000


def main() -> None:
    rng = random.Random(SEED)
    starts = [rng.sample(range(RANGE_MS), DEVICE_COUNT) for _ in range(RUN_COUNT)]
    data = {
        "schema_version": 1,
        "description": (
            "N-sweep v3 initial phases; unsorted generation order is device ID order "
            "and values are unique within each run."
        ),
        "seed": SEED,
        "run_count": RUN_COUNT,
        "master_device_count": DEVICE_COUNT,
        "minimum_ms": 0,
        "maximum_exclusive_ms": RANGE_MS,
        "resolution_ms": 1,
        "sampling": "uniform_without_replacement",
        "start_times_by_run": starts,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
