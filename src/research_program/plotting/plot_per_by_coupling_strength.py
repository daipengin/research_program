from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run
from research_program.config.plot_config import PER_BY_COUPLING_STRENGTH_PLOT_CONFIG
from research_program.io.send_log import (
    add_detection_time_column,
    detection_time_values,
    normalize_send_time_columns,
)
from research_program.plotting.labels import (
    coupling_strength_axis_label,
    coupling_strength_value_label,
)


CFG = PER_BY_COUPLING_STRENGTH_PLOT_CONFIG


def _is_blank(value: object) -> bool:
    return value is None or pd.isna(value) or str(value).strip() == ""


def read_metadata(metadata_path: Path) -> tuple[list[str], str, float]:
    df = pd.read_csv(metadata_path)

    tags_raw = df.loc[0, "tags"] if "tags" in df.columns else ""
    if _is_blank(tags_raw):
        tags: list[str] = []
    else:
        tags = [tag.strip() for tag in str(tags_raw).split(";") if tag.strip()]

    raw_coupling_function = df.loc[0, "coupling_function"] if "coupling_function" in df.columns else None
    if _is_blank(raw_coupling_function):
        coupling_function = "None"
    else:
        coupling_function = str(raw_coupling_function).strip()

    if "coupling_strength" not in df.columns or _is_blank(df.loc[0, "coupling_strength"]):
        raise ValueError(f"coupling_strength is missing: {metadata_path}")
    coupling_strength = float(df.loc[0, "coupling_strength"])

    return tags, coupling_function, coupling_strength


def extract_device_count_from_tags(tags: list[str]) -> int:
    for tag in tags:
        match = re.fullmatch(r"(\d+)dai", tag)
        if match is not None:
            return int(match.group(1))
    raise ValueError(f"device count tag like '20dai' was not found in tags: {tags}")


def read_send_log(send_log_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        send_log_path,
        dtype={
            "time": "float64",
            "oscillator_id": "string",
            "send_count": "int64",
        },
    )
    return df.sort_values(["time", "oscillator_id"]).reset_index(drop=True)


def read_calculated_cycle_data(cycle_data_path: Path) -> tuple[int, np.ndarray, np.ndarray]:
    df = pd.read_csv(cycle_data_path)

    reference_id = int(df.loc[0, "reference_id"])
    cycle_starts = df["cycle_start_time"].to_numpy(dtype=np.float64)
    is_original_cycle = df["is_original_cycle"].to_numpy(dtype=bool)

    return reference_id, cycle_starts, is_original_cycle


def normalize_oscillator_id_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    send_df = send_df.copy()

    if "hex" in tags:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(lambda x: int(str(x), 16))
    else:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(lambda x: int(str(x)))

    return send_df


def normalize_time_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    return add_detection_time_column(normalize_send_time_columns(send_df, tags))


def assign_cycles_from_reference_windows(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
) -> pd.DataFrame:
    df = send_df.copy()
    times = detection_time_values(df)

    cycle_index = np.searchsorted(cycle_starts, times, side="right")
    valid = np.isfinite(times) & (cycle_index > 0) & (cycle_index <= len(cycle_starts))

    df = df.loc[valid].copy()
    df["cycle_index"] = cycle_index[valid].astype(np.int64)

    return df


def compute_per_series(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    num_devices: int,
    window_width_cycles: int,
) -> tuple[np.ndarray, np.ndarray]:
    send_df = assign_cycles_from_reference_windows(send_df, cycle_starts)

    max_cycle = len(cycle_starts)
    if max_cycle < window_width_cycles:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)

    counts_by_cycle = send_df.groupby("cycle_index").size().sort_index()

    counts_full = np.zeros(max_cycle, dtype=np.int64)
    if not counts_by_cycle.empty:
        cycle_indices = counts_by_cycle.index.to_numpy(dtype=np.int64)
        valid_indices = (cycle_indices >= 1) & (cycle_indices <= max_cycle)
        counts_full[cycle_indices[valid_indices] - 1] = counts_by_cycle.to_numpy(dtype=np.int64)[valid_indices]

    kernel = np.ones(window_width_cycles, dtype=np.int64)
    window_counts = np.convolve(counts_full, kernel, mode="valid")

    denominator = float(num_devices * window_width_cycles)
    success_ratio = window_counts / denominator
    per_percent = (1.0 - success_ratio) * 100.0
    per_percent = np.clip(per_percent, 0.0, None)

    x = np.arange(window_width_cycles, max_cycle + 1, dtype=np.int64)
    return x, per_percent


def cycle_at_target_time(cycle_starts: np.ndarray, target_time_ms: float) -> Optional[int]:
    if cycle_starts.size == 0 or not np.isfinite(target_time_ms):
        return None
    if target_time_ms < float(cycle_starts[0]) or target_time_ms > float(cycle_starts[-1]):
        return None

    cycle_index = int(np.searchsorted(cycle_starts, target_time_ms, side="right"))
    if cycle_index <= 0:
        return None
    return cycle_index


def extract_per_at_target_time(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    num_devices: int,
    target_time_ms: float,
    window_width_cycles: int,
) -> tuple[Optional[int], Optional[float]]:
    target_cycle = cycle_at_target_time(cycle_starts, target_time_ms)
    if target_cycle is None:
        return None, None

    x, per_percent = compute_per_series(
        send_df=send_df,
        cycle_starts=cycle_starts,
        num_devices=num_devices,
        window_width_cycles=window_width_cycles,
    )
    if len(x) == 0:
        return target_cycle, None

    mask = x == target_cycle
    if not np.any(mask):
        return target_cycle, None

    return target_cycle, float(per_percent[mask][0])


def config_allows_result(coupling_function: str, coupling_strength: float) -> bool:
    if CFG.target_coupling_functions and coupling_function not in set(CFG.target_coupling_functions):
        return False
    if CFG.coupling_strength_min is not None and coupling_strength < CFG.coupling_strength_min:
        return False
    if CFG.coupling_strength_max is not None and coupling_strength > CFG.coupling_strength_max:
        return False
    return True


def process_run(run_dir: Path) -> Optional[dict]:
    try:
        send_log_path = run_dir / "send_log.csv"
        metadata_path = run_dir / "metadata.csv"
        cycle_data_path = run_dir / "calculated_Cycle_data.csv"

        if not send_log_path.exists() or not metadata_path.exists():
            return None

        if not cycle_data_path.exists():
            ensure_cycle_data_for_run(run_dir)

        if not cycle_data_path.exists():
            return None

        tags, coupling_function, coupling_strength = read_metadata(metadata_path)
        if not config_allows_result(coupling_function, coupling_strength):
            return None

        send_df = read_send_log(send_log_path)
        if send_df.empty:
            return None

        num_devices = extract_device_count_from_tags(tags)
        send_df = normalize_oscillator_id_column(send_df, tags)
        send_df = normalize_time_column(send_df, tags)

        _, cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)

        target_cycle, per_value = extract_per_at_target_time(
            send_df=send_df,
            cycle_starts=cycle_starts,
            num_devices=num_devices,
            target_time_ms=CFG.target_time_ms,
            window_width_cycles=CFG.per_window_width_cycles,
        )

        if target_cycle is None or per_value is None:
            return None

        return {
            "run_id": run_dir.name,
            "coupling_function": coupling_function,
            "coupling_strength": coupling_strength,
            "target_time_ms": float(CFG.target_time_ms),
            "target_cycle": int(target_cycle),
            "per_percent": float(per_value),
        }
    except Exception:
        return None


def _empty_raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "run_id",
            "coupling_function",
            "coupling_strength",
            "target_time_ms",
            "target_cycle",
            "per_percent",
        ]
    )


def _empty_aggregated_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "coupling_function",
            "coupling_strength",
            "per_percent_mean",
            "per_percent_std",
            "per_percent_min",
            "per_percent_max",
            "target_cycle_mean",
            "target_cycle_min",
            "target_cycle_max",
            "count",
        ]
    )


def collect_all_results(results_dir: Path) -> pd.DataFrame:
    run_dirs = sorted([p for p in results_dir.iterdir() if p.is_dir()])
    if not run_dirs:
        return _empty_raw_frame()

    rows = []
    max_workers = min(len(run_dirs), (os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_run, run_dir) for run_dir in run_dirs]

        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                rows.append(result)

    if not rows:
        return _empty_raw_frame()

    return pd.DataFrame(rows)


def aggregate_results(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_aggregated_frame()

    grouped = (
        df.groupby(["coupling_function", "coupling_strength"], as_index=False)
        .agg(
            per_percent_mean=("per_percent", "mean"),
            per_percent_std=("per_percent", "std"),
            per_percent_min=("per_percent", "min"),
            per_percent_max=("per_percent", "max"),
            target_cycle_mean=("target_cycle", "mean"),
            target_cycle_min=("target_cycle", "min"),
            target_cycle_max=("target_cycle", "max"),
            count=("per_percent", "size"),
        )
        .sort_values(["coupling_function", "coupling_strength"])
        .reset_index(drop=True)
    )
    return grouped


def _format_number_for_filename(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", "p")


def get_aggregated_csv_path(output_dir: Path) -> Path:
    time_label = _format_number_for_filename(float(CFG.target_time_ms))
    return output_dir / f"per_by_coupling_strength_time_{time_label}ms_window_{CFG.per_window_width_cycles}.csv"


def save_aggregated_csv(df: pd.DataFrame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = get_aggregated_csv_path(output_dir)
    df.to_csv(output_path, index=False)
    return output_path


def read_aggregated_csv(csv_path: Path) -> pd.DataFrame:
    return pd.read_csv(
        csv_path,
        dtype={
            "coupling_function": "string",
            "coupling_strength": "float64",
            "per_percent_mean": "float64",
            "per_percent_std": "float64",
            "per_percent_min": "float64",
            "per_percent_max": "float64",
            "target_cycle_mean": "float64",
            "target_cycle_min": "int64",
            "target_cycle_max": "int64",
            "count": "int64",
        },
    ).sort_values(["coupling_function", "coupling_strength"]).reset_index(drop=True)


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "unknown"


def _display_coupling_function(coupling_function: str) -> str:
    return "FrogChorus" if coupling_function == "FROGCHORUS" else coupling_function


def minimum_per_row(df: pd.DataFrame) -> Optional[pd.Series]:
    if df.empty or "per_percent_mean" not in df.columns:
        return None

    per_values = pd.to_numeric(df["per_percent_mean"], errors="coerce")
    valid_per_values = per_values.dropna()
    if valid_per_values.empty:
        return None

    return df.loc[valid_per_values.idxmin()]


def format_minimum_per_summary(coupling_function: str, row: pd.Series) -> str:
    display_name = _display_coupling_function(str(coupling_function))
    coupling_strength = float(row["coupling_strength"])
    per_percent = float(row["per_percent_mean"])
    count = int(row["count"]) if "count" in row and not pd.isna(row["count"]) else 0
    return (
        f"min PER: method={display_name}, "
        f"{coupling_strength_value_label(coupling_strength)}, "
        f"PER={per_percent:g}%, count={count}"
    )


def annotate_minimum_per(row: pd.Series) -> None:
    if not CFG.show_min_per_annotation:
        return

    coupling_strength = float(row["coupling_strength"])
    per_percent = float(row["per_percent_mean"])
    label = f"min PER={per_percent:g}%\n{coupling_strength_value_label(coupling_strength)}"

    plt.plot(
        [coupling_strength],
        [per_percent],
        linestyle="None",
        marker="*",
        markersize=CFG.min_per_marker_size,
        color="tab:red",
        zorder=5,
    )
    plt.annotate(
        label,
        xy=(coupling_strength, per_percent),
        xytext=(8, 8),
        textcoords="offset points",
        fontsize=CFG.min_per_annotation_font_size,
        ha="left",
        va="bottom",
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "white",
            "edgecolor": "0.4",
            "alpha": 0.85,
        },
        arrowprops={
            "arrowstyle": "->",
            "linewidth": 0.8,
            "color": "0.3",
        },
    )


def error_bar_yerr(df: pd.DataFrame) -> np.ndarray | None:
    if not CFG.show_error_bars:
        return None

    mode = str(getattr(CFG, "error_bar_mode", "std"))
    if mode == "min_max":
        required_columns = {"per_percent_mean", "per_percent_min", "per_percent_max"}
        if not required_columns.issubset(df.columns):
            return None
        mean = df["per_percent_mean"].to_numpy(dtype=np.float64)
        minimum = df["per_percent_min"].to_numpy(dtype=np.float64)
        maximum = df["per_percent_max"].to_numpy(dtype=np.float64)
        lower = np.clip(mean - minimum, 0.0, None)
        upper = np.clip(maximum - mean, 0.0, None)
        return np.vstack([lower, upper])

    if mode == "std" and "per_percent_std" in df.columns:
        return df["per_percent_std"].fillna(0.0).to_numpy(dtype=np.float64)

    return None


def save_plots(df: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    if df.empty:
        return output_paths

    time_label = _format_number_for_filename(float(CFG.target_time_ms))

    for coupling_function, sub in df.groupby("coupling_function"):
        sub = sub.sort_values("coupling_strength").reset_index(drop=True)
        if sub.empty:
            continue

        plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

        yerr = error_bar_yerr(sub)

        if yerr is None:
            plt.plot(
                sub["coupling_strength"],
                sub["per_percent_mean"],
                linestyle=CFG.line_style,
                marker=CFG.marker_style,
                markersize=CFG.marker_size,
                linewidth=CFG.line_width,
            )
        else:
            plt.errorbar(
                sub["coupling_strength"],
                sub["per_percent_mean"],
                yerr=yerr,
                linestyle=CFG.line_style,
                marker=CFG.marker_style,
                markersize=CFG.marker_size,
                linewidth=CFG.line_width,
                capsize=CFG.error_bar_capsize,
            )

        min_row = minimum_per_row(sub)
        if min_row is not None:
            annotate_minimum_per(min_row)

        if CFG.xlim_min is not None or CFG.xlim_max is not None:
            plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

        if CFG.ylim_min is not None or CFG.ylim_max is not None:
            plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

        plt.xlabel(coupling_strength_axis_label(CFG.x_label), fontsize=CFG.font_size_label)
        plt.ylabel(CFG.y_label, fontsize=CFG.font_size_label)

        display_name = _display_coupling_function(str(coupling_function))
        if CFG.show_title:
            plt.title(
                f"PER at {CFG.target_time_ms:g} ms\nmethod={display_name}, window={CFG.per_window_width_cycles} cycles",
                fontsize=CFG.font_size_title,
            )

        plt.xticks(fontsize=CFG.font_size_ticks)
        plt.yticks(fontsize=CFG.font_size_ticks)
        plt.grid(True)
        plt.tight_layout()

        safe_function = _safe_filename_part(str(coupling_function))
        output_path = output_dir / f"{safe_function}_per_by_k_time_{time_label}ms.pdf"
        plt.savefig(output_path, dpi=CFG.save_dpi)
        plt.close()
        output_paths.append(output_path)

    return output_paths


def main() -> None:
    results_dir = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", CFG.results_dir))
    force_recalculate = os.environ.get("RESEARCH_PROGRAM_FORCE_RECALCULATE") == "1"
    style_only_redraw = os.environ.get("RESEARCH_PROGRAM_STYLE_ONLY_REDRAW") == "1"

    CFG.graphs_dir.mkdir(parents=True, exist_ok=True)
    aggregated_csv_path = get_aggregated_csv_path(CFG.graphs_dir)

    if style_only_redraw:
        if not aggregated_csv_path.exists():
            raise FileNotFoundError(f"style-only redraw needs existing csv: {aggregated_csv_path}")
        agg_df = read_aggregated_csv(aggregated_csv_path)
        print(f"loaded existing csv: {aggregated_csv_path}")
    elif CFG.use_existing_csv_if_available and aggregated_csv_path.exists() and not force_recalculate:
        agg_df = read_aggregated_csv(aggregated_csv_path)
        print(f"loaded existing csv: {aggregated_csv_path}")
    else:
        if not results_dir.exists():
            raise FileNotFoundError(f"results folder not found: {results_dir}")
        raw_df = collect_all_results(results_dir)
        agg_df = aggregate_results(raw_df)
        csv_path = save_aggregated_csv(agg_df, CFG.graphs_dir)
        print(f"saved: {csv_path}")

    plot_paths = save_plots(agg_df, CFG.graphs_dir)
    if not plot_paths:
        print("no PER-by-coupling-strength plots were generated")
        return

    for path in plot_paths:
        print(f"saved: {path}")
    for coupling_function, sub in agg_df.groupby("coupling_function"):
        min_row = minimum_per_row(sub.sort_values("coupling_strength").reset_index(drop=True))
        if min_row is not None:
            print(format_minimum_per_summary(str(coupling_function), min_row))


if __name__ == "__main__":
    main()
