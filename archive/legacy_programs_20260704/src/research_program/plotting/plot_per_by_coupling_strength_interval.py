from __future__ import annotations

import os
import re
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
import uuid

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run
from research_program.config.plot_config import PER_BY_COUPLING_STRENGTH_INTERVAL_PLOT_CONFIG
from research_program.plotting.labels import (
    coupling_strength_axis_label,
    coupling_strength_value_label,
)
from research_program.plotting.plot_per_by_coupling_strength import (
    assign_cycles_from_reference_windows,
    extract_device_count_from_tags,
    normalize_oscillator_id_column,
    normalize_time_column,
    read_calculated_cycle_data,
    read_metadata,
    read_send_log,
)


CFG = PER_BY_COUPLING_STRENGTH_INTERVAL_PLOT_CONFIG
INTERVAL_PER_CACHE_VERSION = 1
INTERVAL_PER_CACHE_DIR = Path(
    os.environ.get(
        "RESEARCH_PROGRAM_INTERVAL_PER_CACHE_DIR",
        "outputs/cache/interval_per_cycle_counts",
    )
)


def config_allows_result(coupling_function: str, coupling_strength: float) -> bool:
    if CFG.target_coupling_functions and coupling_function not in set(CFG.target_coupling_functions):
        return False
    if CFG.coupling_strength_min is not None and coupling_strength < CFG.coupling_strength_min:
        return False
    if CFG.coupling_strength_max is not None and coupling_strength > CFG.coupling_strength_max:
        return False
    return True


def interval_cycle_indices(
    cycle_starts: np.ndarray,
    interval_start_ms: float,
    interval_end_ms: float,
) -> np.ndarray:
    if not np.isfinite(interval_start_ms) or not np.isfinite(interval_end_ms):
        return np.array([], dtype=np.int64)
    if interval_end_ms <= interval_start_ms:
        return np.array([], dtype=np.int64)

    mask = (cycle_starts >= interval_start_ms) & (cycle_starts < interval_end_ms)
    return np.flatnonzero(mask).astype(np.int64) + 1


def _file_signature(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _small_file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_interval_cache_signature(run_dir: Path) -> dict:
    send_log_path = run_dir / "send_log.csv"
    metadata_path = run_dir / "metadata.csv"
    cycle_data_path = run_dir / "calculated_Cycle_data.csv"
    return {
        "version": INTERVAL_PER_CACHE_VERSION,
        "run_id": run_dir.name,
        "send_log": _file_signature(send_log_path),
        "metadata": _file_signature(metadata_path),
        "metadata_sha256": _small_file_hash(metadata_path),
        "cycle_data": _file_signature(cycle_data_path),
    }


def run_interval_cache_path(run_dir: Path) -> Path:
    signature = run_interval_cache_signature(run_dir)
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return INTERVAL_PER_CACHE_DIR / f"{hashlib.sha256(encoded).hexdigest()}.npz"


def build_cycle_count_cache(send_df: pd.DataFrame, cycle_starts: np.ndarray) -> dict[str, np.ndarray]:
    send_df = assign_cycles_from_reference_windows(send_df, cycle_starts)
    max_cycle = len(cycle_starts)

    counts_full = np.zeros(max_cycle, dtype=np.int64)
    if max_cycle > 0 and not send_df.empty:
        counts_by_cycle = send_df.groupby("cycle_index").size().sort_index()
        cycle_indices = counts_by_cycle.index.to_numpy(dtype=np.int64)
        valid_indices = (cycle_indices >= 1) & (cycle_indices <= max_cycle)
        counts_full[cycle_indices[valid_indices] - 1] = counts_by_cycle.to_numpy(dtype=np.int64)[valid_indices]

    cumulative_counts = np.concatenate(
        [np.array([0], dtype=np.int64), np.cumsum(counts_full, dtype=np.int64)]
    )
    return {
        "cycle_starts": np.asarray(cycle_starts, dtype=np.float64),
        "cumulative_counts": cumulative_counts,
    }


def save_cycle_count_cache(cache_path: Path, cache_data: dict[str, np.ndarray]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("wb") as handle:
            np.savez(
                handle,
                cycle_starts=cache_data["cycle_starts"],
                cumulative_counts=cache_data["cumulative_counts"],
            )
        tmp_path.replace(cache_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def load_or_build_cycle_count_cache(
    run_dir: Path,
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
) -> dict[str, np.ndarray]:
    cache_path = run_interval_cache_path(run_dir)
    cached = load_cycle_count_cache_if_available(run_dir)
    if cached is not None:
        return cached

    cache_data = build_cycle_count_cache(send_df, cycle_starts)
    save_cycle_count_cache(cache_path, cache_data)
    return cache_data


def load_cycle_count_cache_if_available(run_dir: Path) -> dict[str, np.ndarray] | None:
    cache_path = run_interval_cache_path(run_dir)
    if cache_path.exists():
        try:
            with np.load(cache_path) as loaded:
                cached_cycle_starts = loaded["cycle_starts"].astype(np.float64, copy=False)
                cached_cumulative_counts = loaded["cumulative_counts"].astype(np.int64, copy=False)
                if cached_cumulative_counts.size == cached_cycle_starts.size + 1:
                    return {
                        "cycle_starts": cached_cycle_starts,
                        "cumulative_counts": cached_cumulative_counts,
                    }
        except Exception:
            pass
    return None


def compute_interval_per_from_cycle_counts(
    cycle_starts: np.ndarray,
    cumulative_counts: np.ndarray,
    num_devices: int,
    interval_start_ms: float,
    interval_end_ms: float,
) -> Optional[dict]:
    if not np.isfinite(interval_start_ms) or not np.isfinite(interval_end_ms):
        return None
    if interval_end_ms <= interval_start_ms:
        return None
    if cycle_starts.size == 0 or cumulative_counts.size != cycle_starts.size + 1:
        return None

    start_index = int(np.searchsorted(cycle_starts, interval_start_ms, side="left"))
    end_index = int(np.searchsorted(cycle_starts, interval_end_ms, side="left"))
    interval_cycle_count = end_index - start_index
    if interval_cycle_count <= 0:
        return None

    actual_packets = int(cumulative_counts[end_index] - cumulative_counts[start_index])
    expected_packets = int(interval_cycle_count * num_devices)
    if expected_packets <= 0:
        return None

    success_ratio = actual_packets / float(expected_packets)
    per_percent = max(0.0, (1.0 - success_ratio) * 100.0)
    return {
        "interval_cycle_count": int(interval_cycle_count),
        "expected_packets": expected_packets,
        "actual_packets": actual_packets,
        "per_percent": float(per_percent),
    }


def compute_interval_per(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    num_devices: int,
    interval_start_ms: float,
    interval_end_ms: float,
) -> Optional[dict]:
    target_cycles = interval_cycle_indices(cycle_starts, interval_start_ms, interval_end_ms)
    if target_cycles.size == 0:
        return None

    send_df = assign_cycles_from_reference_windows(send_df, cycle_starts)
    actual_packets = int(send_df["cycle_index"].isin(target_cycles).sum())
    expected_packets = int(target_cycles.size * num_devices)
    if expected_packets <= 0:
        return None

    success_ratio = actual_packets / float(expected_packets)
    per_percent = max(0.0, (1.0 - success_ratio) * 100.0)
    return {
        "interval_cycle_count": int(target_cycles.size),
        "expected_packets": expected_packets,
        "actual_packets": actual_packets,
        "per_percent": float(per_percent),
    }


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

        num_devices = extract_device_count_from_tags(tags)
        cycle_count_cache = load_cycle_count_cache_if_available(run_dir)
        if cycle_count_cache is None:
            send_df = read_send_log(send_log_path)
            send_df = normalize_oscillator_id_column(send_df, tags)
            send_df = normalize_time_column(send_df, tags)

            _, cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)
            cycle_count_cache = load_or_build_cycle_count_cache(
                run_dir=run_dir,
                send_df=send_df,
                cycle_starts=cycle_starts,
            )

        metrics = compute_interval_per_from_cycle_counts(
            cycle_starts=cycle_count_cache["cycle_starts"],
            cumulative_counts=cycle_count_cache["cumulative_counts"],
            num_devices=num_devices,
            interval_start_ms=CFG.interval_start_ms,
            interval_end_ms=CFG.interval_end_ms,
        )
        if metrics is None:
            return None

        return {
            "run_id": run_dir.name,
            "coupling_function": coupling_function,
            "coupling_strength": coupling_strength,
            "interval_start_ms": float(CFG.interval_start_ms),
            "interval_end_ms": float(CFG.interval_end_ms),
            **metrics,
        }
    except Exception:
        return None


def _empty_raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "run_id",
            "coupling_function",
            "coupling_strength",
            "interval_start_ms",
            "interval_end_ms",
            "interval_cycle_count",
            "expected_packets",
            "actual_packets",
            "per_percent",
        ]
    )


def _empty_aggregated_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "coupling_function",
            "coupling_strength",
            "interval_start_ms",
            "interval_end_ms",
            "per_percent_mean",
            "per_percent_std",
            "per_percent_min",
            "per_percent_max",
            "expected_packets_sum",
            "actual_packets_sum",
            "interval_cycle_count_mean",
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

    return (
        df.groupby(["coupling_function", "coupling_strength"], as_index=False)
        .agg(
            interval_start_ms=("interval_start_ms", "first"),
            interval_end_ms=("interval_end_ms", "first"),
            per_percent_mean=("per_percent", "mean"),
            per_percent_std=("per_percent", "std"),
            per_percent_min=("per_percent", "min"),
            per_percent_max=("per_percent", "max"),
            expected_packets_sum=("expected_packets", "sum"),
            actual_packets_sum=("actual_packets", "sum"),
            interval_cycle_count_mean=("interval_cycle_count", "mean"),
            count=("per_percent", "size"),
        )
        .sort_values(["coupling_function", "coupling_strength"])
        .reset_index(drop=True)
    )


def _format_number_for_filename(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", "p")


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "unknown"


def graph_stem_for_coupling_function(coupling_function: str) -> str:
    start_label = _format_number_for_filename(float(CFG.interval_start_ms))
    end_label = _format_number_for_filename(float(CFG.interval_end_ms))
    safe_function = _safe_filename_part(str(coupling_function))
    return f"{safe_function}_per_by_k_interval_{start_label}_to_{end_label}ms"


def graph_data_csv_path(output_dir: Path, coupling_function: str) -> Path:
    return output_dir / f"{graph_stem_for_coupling_function(coupling_function)}.csv"


def graph_data_csv_paths(output_dir: Path) -> list[Path]:
    start_label = _format_number_for_filename(float(CFG.interval_start_ms))
    end_label = _format_number_for_filename(float(CFG.interval_end_ms))
    return sorted(output_dir.glob(f"*_per_by_k_interval_{start_label}_to_{end_label}ms.csv"))


def save_graph_data_csvs(df: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    if df.empty:
        return output_paths

    for coupling_function, sub in df.groupby("coupling_function"):
        output_path = graph_data_csv_path(output_dir, str(coupling_function))
        sub.sort_values("coupling_strength").to_csv(output_path, index=False)
        output_paths.append(output_path)
    return output_paths


def read_aggregated_csv(csv_path: Path) -> pd.DataFrame:
    return pd.read_csv(
        csv_path,
        dtype={
            "coupling_function": "string",
            "coupling_strength": "float64",
            "interval_start_ms": "float64",
            "interval_end_ms": "float64",
            "per_percent_mean": "float64",
            "per_percent_std": "float64",
            "per_percent_min": "float64",
            "per_percent_max": "float64",
            "expected_packets_sum": "int64",
            "actual_packets_sum": "int64",
            "interval_cycle_count_mean": "float64",
            "count": "int64",
        },
    ).sort_values(["coupling_function", "coupling_strength"]).reset_index(drop=True)


def read_graph_data_csvs(output_dir: Path) -> pd.DataFrame:
    csv_paths = graph_data_csv_paths(output_dir)
    if csv_paths:
        frames = [read_aggregated_csv(path) for path in csv_paths]
        return pd.concat(frames, ignore_index=True).sort_values(
            ["coupling_function", "coupling_strength"]
        ).reset_index(drop=True)

    raise FileNotFoundError(
        f"style-only redraw needs existing per-graph csvs like: {output_dir / '*_per_by_k_interval_*_to_*ms.csv'}"
    )


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
        f"min interval PER: method={display_name}, "
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
                f"PER from {CFG.interval_start_ms:g} to {CFG.interval_end_ms:g} ms\nmethod={display_name}",
                fontsize=CFG.font_size_title,
            )

        plt.xticks(fontsize=CFG.font_size_ticks)
        plt.yticks(fontsize=CFG.font_size_ticks)
        plt.grid(True)
        plt.tight_layout()

        output_path = output_dir / f"{graph_stem_for_coupling_function(str(coupling_function))}.pdf"
        plt.savefig(output_path, dpi=CFG.save_dpi)
        plt.close()
        output_paths.append(output_path)

    return output_paths


def main() -> None:
    results_dir = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", CFG.results_dir))
    force_recalculate = os.environ.get("RESEARCH_PROGRAM_FORCE_RECALCULATE") == "1"
    style_only_redraw = os.environ.get("RESEARCH_PROGRAM_STYLE_ONLY_REDRAW") == "1"

    CFG.graphs_dir.mkdir(parents=True, exist_ok=True)
    if style_only_redraw:
        agg_df = read_graph_data_csvs(CFG.graphs_dir)
        print(f"loaded existing per-graph csvs: {CFG.graphs_dir}")
    elif CFG.use_existing_csv_if_available and graph_data_csv_paths(CFG.graphs_dir) and not force_recalculate:
        agg_df = read_graph_data_csvs(CFG.graphs_dir)
        print(f"loaded existing per-graph csvs: {CFG.graphs_dir}")
    else:
        if not results_dir.exists():
            raise FileNotFoundError(f"results folder not found: {results_dir}")
        raw_df = collect_all_results(results_dir)
        agg_df = aggregate_results(raw_df)
        csv_paths = save_graph_data_csvs(agg_df, CFG.graphs_dir)
        for csv_path in csv_paths:
            print(f"saved: {csv_path}")

    plot_paths = save_plots(agg_df, CFG.graphs_dir)
    if not plot_paths:
        print("no interval PER-by-coupling-strength plots were generated")
        return

    for path in plot_paths:
        print(f"saved: {path}")
    for coupling_function, sub in agg_df.groupby("coupling_function"):
        min_row = minimum_per_row(sub.sort_values("coupling_strength").reset_index(drop=True))
        if min_row is not None:
            print(format_minimum_per_summary(str(coupling_function), min_row))


if __name__ == "__main__":
    main()
