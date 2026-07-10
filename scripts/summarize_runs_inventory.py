from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from research_program.analysis import calculate_cycle_data
from research_program.analysis import calculate_phase_gap_error
from research_program.io import sqlite_runs

DEFAULT_INPUT_PATH = PROJECT_ROOT / "results" / "inventory" / "existing_runs_inventory.csv"
DEFAULT_SUMMARY_PATH = PROJECT_ROOT / "results" / "inventory" / "runs_summary.csv"
DEFAULT_SWEEP_PATH = PROJECT_ROOT / "results" / "inventory" / "sweep_overview.csv"
DEFAULT_SPOT_CHECK_PATH = PROJECT_ROOT / "results" / "inventory" / "sendlog_spot_check.csv"
PIPELINE_CHECK_DIR = PROJECT_ROOT / "outputs" / "cache" / "inventory_pipeline_check"

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
    parser.add_argument("--spot-check-output", type=Path, default=DEFAULT_SPOT_CHECK_PATH)
    args = parser.parse_args()

    inventory = pd.read_csv(resolve_path(args.input), dtype="string")
    if inventory.empty:
        raise ValueError(f"inventory is empty: {args.input}")

    inventory = normalize_inventory(inventory)
    inventory = attach_allocated_data_sizes(inventory)

    summary = build_runs_summary(inventory)
    sweep = build_sweep_overview(inventory)
    spot_check = build_sendlog_spot_check(inventory)

    write_csv(summary, args.summary_output)
    write_csv(sweep, args.sweep_output)
    write_csv(spot_check, args.spot_check_output)
    print(f"wrote {len(summary)} rows: {args.summary_output}")
    print(f"wrote {len(sweep)} rows: {args.sweep_output}")
    print(f"wrote {len(spot_check)} rows: {args.spot_check_output}")
    return 0


def normalize_inventory(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in GROUP_COLUMNS + ["analysis_readable", "created_at", "path"]:
        if column not in out.columns:
            out[column] = "unknown"
        out[column] = out[column].fillna("unknown").astype("string")
    out["k_numeric"] = pd.to_numeric(out["k"], errors="coerce")
    if "send_log_rows" not in out.columns:
        out["send_log_rows"] = "0"
    out["send_log_rows_numeric"] = pd.to_numeric(
        out["send_log_rows"],
        errors="coerce",
    ).fillna(0).astype("int64")
    if "has_cycle_cache" not in out.columns:
        out["has_cycle_cache"] = "unknown"
    out["has_cycle_cache"] = out["has_cycle_cache"].fillna("unknown").astype("string")
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
                "total_data_size_bytes": exact_function_data_size_bytes(group),
            }
        )
    return pd.DataFrame(rows).sort_values("coupling_function").reset_index(drop=True)


def build_sendlog_spot_check(df: pd.DataFrame) -> pd.DataFrame:
    sample_rows: list[pd.Series] = []
    readable = df[df["send_log_rows_numeric"] > 0].copy()
    for _, function_group in readable.groupby("coupling_function", dropna=False):
        k_values = sorted(function_group["k_numeric"].dropna().unique().tolist())
        for k_value in sample_k_values(k_values, sample_count=5):
            k_group = function_group[function_group["k_numeric"] == k_value]
            sample_rows.extend(
                row for _, row in k_group.sort_values("run_id").head(3).iterrows()
            )

    pipeline_result = run_pipeline_smoke_test(sample_rows[0]) if sample_rows else {
        "pipeline_test_run_id": "unknown",
        "pipeline_test_passed": "no",
        "pipeline_test_detail": "no readable send_log sample was available",
    }

    rows: list[dict[str, object]] = []
    for row in sample_rows:
        expected = expected_send_log_rows(row)
        measured = int(row["send_log_rows_numeric"])
        rows.append(
            {
                "coupling_function": row["coupling_function"],
                "k": row["k"],
                "run_id": row["run_id"],
                "device_count": row["device_count"],
                "simulation_length_cycles": row["simulation_length_cycles"],
                "expected_send_log_rows": expected,
                "measured_send_log_rows": measured,
                "difference": "unknown" if expected == "unknown" else measured - int(expected),
                "has_cycle_cache": row["has_cycle_cache"],
                **pipeline_result,
            }
        )
    return pd.DataFrame(rows)


def sample_k_values(k_values: list[float], sample_count: int) -> list[float]:
    if len(k_values) <= sample_count:
        return k_values
    indexes = sorted(
        {
            round(index * (len(k_values) - 1) / (sample_count - 1))
            for index in range(sample_count)
        }
    )
    return [k_values[int(index)] for index in indexes]


def expected_send_log_rows(row: pd.Series) -> str:
    try:
        device_count = float(row["device_count"])
        cycle_count = float(row["simulation_length_cycles"])
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(device_count) or not math.isfinite(cycle_count):
        return "unknown"
    return str(int(round(device_count * cycle_count)))


def run_pipeline_smoke_test(row: pd.Series) -> dict[str, str]:
    run_id = str(row["run_id"])
    base_path = resolve_path(Path(str(row["base_path"])))
    raw_db_path = base_path / "raw_run.sqlite"
    if not raw_db_path.exists():
        return {
            "pipeline_test_run_id": run_id,
            "pipeline_test_passed": "no",
            "pipeline_test_detail": f"raw_run.sqlite not found: {raw_db_path}",
        }
    output_dir = PIPELINE_CHECK_DIR / safe_filename(run_id)
    try:
        sqlite_runs.export_run_to_directory(raw_db_path, run_id, output_dir)
        cycle_path = calculate_cycle_data.ensure_cycle_data_for_run(output_dir)
        send_df = calculate_phase_gap_error.read_send_log(output_dir / "send_log.csv")
        tags, num_devices = calculate_phase_gap_error.read_metadata(output_dir / "metadata.csv")
        send_df = calculate_phase_gap_error.normalize_oscillator_id_column(send_df, tags)
        send_df = calculate_phase_gap_error.normalize_time_column(send_df, tags)
        cycle_starts, _ = calculate_phase_gap_error.read_calculated_cycle_data(cycle_path)
        phase_df = calculate_phase_gap_error.compute_mean_abs_gap_error_per_cycle(
            send_df=send_df,
            cycle_starts=cycle_starts,
            num_devices=num_devices,
        )
        if phase_df.empty:
            raise ValueError("phase gap computation returned no rows")
    except Exception as exc:
        return {
            "pipeline_test_run_id": run_id,
            "pipeline_test_passed": "no",
            "pipeline_test_detail": str(exc),
        }
    return {
        "pipeline_test_run_id": run_id,
        "pipeline_test_passed": "yes",
        "pipeline_test_detail": f"cycle_rows={len(cycle_starts)}, phase_rows={len(phase_df)}",
    }


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


def exact_function_data_size_bytes(group: pd.DataFrame) -> int:
    total = 0
    seen: set[str] = set()
    for base_path in group["base_path"].dropna().astype(str):
        if base_path in seen:
            continue
        seen.add(base_path)
        total += path_size_bytes(resolve_path(Path(base_path)))
    return int(total)


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


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value) or "run"


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
