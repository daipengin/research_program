from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "results" / "inventory" / "existing_runs_inventory.csv"
DEFAULT_SUMMARY_PATH = PROJECT_ROOT / "results" / "inventory" / "runs_summary.csv"
DEFAULT_SWEEP_PATH = PROJECT_ROOT / "results" / "inventory" / "sweep_overview.csv"

GROUP_COLUMNS = [
    "coupling_function",
    "k",
    "device_count",
    "cycle_time",
    "listening_rate",
    "duration",
    "format",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize run-level inventory into condition and sweep overview CSVs."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--sweep-output", type=Path, default=DEFAULT_SWEEP_PATH)
    args = parser.parse_args()

    inventory = pd.read_csv(resolve_path(args.input), dtype="string")
    if inventory.empty:
        raise ValueError(f"inventory is empty: {args.input}")

    inventory = normalize_inventory(inventory)
    inventory = attach_allocated_data_sizes(inventory)

    summary = build_runs_summary(inventory)
    sweep = build_sweep_overview(inventory)

    write_csv(summary, args.summary_output)
    write_csv(sweep, args.sweep_output)
    print(f"wrote {len(summary)} rows: {args.summary_output}")
    print(f"wrote {len(sweep)} rows: {args.sweep_output}")
    return 0


def normalize_inventory(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in GROUP_COLUMNS + ["analysis_readable", "created_at", "path"]:
        if column not in out.columns:
            out[column] = "unknown"
        out[column] = out[column].fillna("unknown").astype("string")
    out["k_numeric"] = pd.to_numeric(out["k"], errors="coerce")
    out["base_path"] = out["path"].map(base_path_from_inventory_path).astype("string")
    out["created_at_dt"] = pd.to_datetime(out["created_at"], errors="coerce", utc=True)
    return out


def attach_allocated_data_sizes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    counts_by_base = out["base_path"].value_counts(dropna=False).to_dict()
    size_by_base: dict[str, int] = {}
    for base_path in counts_by_base:
        size_by_base[str(base_path)] = path_size_bytes(resolve_path(Path(str(base_path))))

    out["base_data_size_bytes"] = out["base_path"].map(
        lambda value: size_by_base.get(str(value), 0)
    )
    out["base_run_count"] = out["base_path"].map(
        lambda value: int(counts_by_base.get(value, counts_by_base.get(str(value), 1)))
    )
    out["allocated_data_size_bytes"] = (
        out["base_data_size_bytes"].astype("float64")
        / out["base_run_count"].clip(lower=1).astype("float64")
    )
    return out


def build_runs_summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(GROUP_COLUMNS, dropna=False)
        .agg(
            run_count=("path", "size"),
            readable_run_count=("analysis_readable", lambda s: int((s == "yes").sum())),
            total_data_size_bytes=("allocated_data_size_bytes", "sum"),
            oldest_created_at=("created_at_dt", "min"),
            newest_created_at=("created_at_dt", "max"),
        )
        .reset_index()
    )
    grouped["total_data_size_bytes"] = grouped["total_data_size_bytes"].round().astype("int64")
    grouped["oldest_created_at"] = grouped["oldest_created_at"].map(format_timestamp)
    grouped["newest_created_at"] = grouped["newest_created_at"].map(format_timestamp)
    return grouped.sort_values(GROUP_COLUMNS).reset_index(drop=True)


def build_sweep_overview(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for coupling_function, group in df.groupby("coupling_function", dropna=False):
        k_values = sorted(
            {
                float(value)
                for value in group["k_numeric"].dropna().tolist()
                if math.isfinite(float(value))
            }
        )
        step_values = sorted(
            {
                round(k_values[index + 1] - k_values[index], 10)
                for index in range(len(k_values) - 1)
            }
        )
        rows.append(
            {
                "coupling_function": coupling_function,
                "k_min": format_number(k_values[0]) if k_values else "unknown",
                "k_max": format_number(k_values[-1]) if k_values else "unknown",
                "k_count": len(k_values),
                "k_step_values": ";".join(format_number(value) for value in step_values)
                if step_values
                else "unknown",
                "device_counts": join_unique(group["device_count"]),
                "cycle_times": join_unique(group["cycle_time"]),
                "listening_rates": join_unique(group["listening_rate"]),
                "durations": join_unique(group["duration"]),
                "formats": join_unique(group["format"]),
                "run_count": int(len(group)),
                "readable_run_count": int((group["analysis_readable"] == "yes").sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("coupling_function").reset_index(drop=True)


def base_path_from_inventory_path(value: object) -> str:
    text = str(value)
    if "::" in text:
        text = text.split("::", 1)[0]
    return text


def path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        iterator = path.rglob("*")
        for child in iterator:
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def join_unique(series: pd.Series) -> str:
    values = sorted({str(value) for value in series.dropna().tolist() if str(value) != ""})
    return ";".join(values) if values else "unknown"


def format_timestamp(value: object) -> str:
    if pd.isna(value):
        return "unknown"
    return pd.Timestamp(value).isoformat()


def format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):g}"


def resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def write_csv(df: pd.DataFrame, path: Path) -> None:
    output_path = resolve_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


if __name__ == "__main__":
    raise SystemExit(main())
