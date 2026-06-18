from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from calculate_phase_gap_error import ensure_phase_gap_error_for_run


RESULTS_DIR = Path("results")
OUTPUT_DIR = Path("aggregated_stats")


def read_metadata(metadata_path: Path) -> tuple[str, int]:
    df = pd.read_csv(metadata_path)

    coupling_function = str(df.loc[0, "coupling_function"])
    coupling_strength = int(df.loc[0, "coupling_strength"])

    return coupling_function, coupling_strength


def read_phase_gap_error(phase_gap_error_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        phase_gap_error_path,
        dtype={
            "cycle_index": "int64",
            "mean_abs_diff_from_ideal_phase_gap": "float64",
            "mean_abs_diff_from_ideal_phase_gap_ratio": "float64",
        },
    )
    return df


def collect_all_data(results_dir: Path) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []

    run_dirs = sorted([p for p in results_dir.iterdir() if p.is_dir()])

    for run_dir in run_dirs:
        metadata_path = run_dir / "metadata.csv"
        phase_gap_error_path = run_dir / "phase_gap_error.csv"

        if not metadata_path.exists():
            print(f"skip: {run_dir} (missing metadata.csv)")
            continue

        if not phase_gap_error_path.exists():
            ensure_phase_gap_error_for_run(run_dir)

        if not phase_gap_error_path.exists():
            print(f"skip: {run_dir} (failed to create phase_gap_error.csv)")
            continue

        coupling_function, coupling_strength = read_metadata(metadata_path)
        phase_df = read_phase_gap_error(phase_gap_error_path)

        if phase_df.empty:
            print(f"skip: {run_dir} (empty phase_gap_error.csv)")
            continue

        phase_df = phase_df.copy()
        phase_df["run_id"] = run_dir.name
        phase_df["coupling_function"] = coupling_function
        phase_df["coupling_strength"] = coupling_strength

        rows.append(phase_df)

    if not rows:
        return pd.DataFrame(
            columns=[
                "run_id",
                "cycle_index",
                "mean_abs_diff_from_ideal_phase_gap",
                "mean_abs_diff_from_ideal_phase_gap_ratio",
                "coupling_function",
                "coupling_strength",
            ]
        )

    return pd.concat(rows, ignore_index=True)


def aggregate_stats_for_group(group_df: pd.DataFrame) -> pd.DataFrame:
    target_df = group_df.dropna(subset=["mean_abs_diff_from_ideal_phase_gap_ratio"]).copy()

    if target_df.empty:
        return pd.DataFrame(
            columns=[
                "cycle_index",
                "count",
                "mean",
                "min",
                "max",
                "median",
                "std",
                "q25",
                "q75",
            ]
        )

    grouped = (
        target_df.groupby("cycle_index", as_index=False)["mean_abs_diff_from_ideal_phase_gap_ratio"]
        .agg(["count", "mean", "min", "max", "median", "std"])
        .reset_index()
    )

    q25 = (
        target_df.groupby("cycle_index")["mean_abs_diff_from_ideal_phase_gap_ratio"]
        .quantile(0.25)
        .reset_index(name="q25")
    )

    q75 = (
        target_df.groupby("cycle_index")["mean_abs_diff_from_ideal_phase_gap_ratio"]
        .quantile(0.75)
        .reset_index(name="q75")
    )

    result = grouped.merge(q25, on="cycle_index", how="left").merge(q75, on="cycle_index", how="left")
    result = result.sort_values("cycle_index").reset_index(drop=True)
    return result


def make_output_filename(coupling_function: str, coupling_strength: int) -> str:
    safe_function = coupling_function.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return f"{safe_function}_{coupling_strength}.csv"


def main() -> None:
    if not RESULTS_DIR.exists():
        raise FileNotFoundError(f"results folder not found: {RESULTS_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_df = collect_all_data(RESULTS_DIR)

    if all_df.empty:
        print("no data collected")
        return

    grouped = all_df.groupby(["coupling_function", "coupling_strength"], sort=True)

    for (coupling_function, coupling_strength), group_df in grouped:
        stats_df = aggregate_stats_for_group(group_df)

        output_filename = make_output_filename(coupling_function, int(coupling_strength))
        output_path = OUTPUT_DIR / output_filename

        stats_df.to_csv(output_path, index=False)
        print(f"saved: {output_path}")


if __name__ == "__main__":
    main()