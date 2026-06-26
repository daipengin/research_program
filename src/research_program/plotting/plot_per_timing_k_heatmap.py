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
from research_program.config.plot_config import PER_TIMING_K_HEATMAP_CONFIG
from research_program.plotting.plot_per_by_coupling_strength import (
    compute_per_series,
    cycle_at_target_time,
    extract_device_count_from_tags,
    normalize_oscillator_id_column,
    normalize_time_column,
    read_calculated_cycle_data,
    read_metadata,
    read_send_log,
)
from research_program.plotting.labels import coupling_strength_axis_label


CFG = PER_TIMING_K_HEATMAP_CONFIG


def _format_number_for_filename(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", "p")


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "unknown"


def _display_coupling_function(coupling_function: str) -> str:
    return "FrogChorus" if coupling_function == "FROGCHORUS" else coupling_function


def timing_grid_ms() -> np.ndarray:
    if CFG.timing_step_ms <= 0:
        raise ValueError("timing_step_ms must be positive")
    if CFG.timing_max_ms < CFG.timing_min_ms:
        raise ValueError("timing_max_ms must be greater than or equal to timing_min_ms")

    count = int(np.floor((CFG.timing_max_ms - CFG.timing_min_ms) / CFG.timing_step_ms)) + 1
    return CFG.timing_min_ms + np.arange(count, dtype=np.float64) * CFG.timing_step_ms


def config_allows_result(coupling_function: str, coupling_strength: float) -> bool:
    if CFG.target_coupling_functions and coupling_function not in set(CFG.target_coupling_functions):
        return False
    if CFG.coupling_strength_min is not None and coupling_strength < CFG.coupling_strength_min:
        return False
    if CFG.coupling_strength_max is not None and coupling_strength > CFG.coupling_strength_max:
        return False
    return True


def process_run(run_dir: Path) -> list[dict]:
    try:
        send_log_path = run_dir / "send_log.csv"
        metadata_path = run_dir / "metadata.csv"
        cycle_data_path = run_dir / "calculated_Cycle_data.csv"

        if not send_log_path.exists() or not metadata_path.exists():
            return []

        if not cycle_data_path.exists():
            ensure_cycle_data_for_run(run_dir)

        if not cycle_data_path.exists():
            return []

        tags, coupling_function, coupling_strength = read_metadata(metadata_path)
        if not config_allows_result(coupling_function, coupling_strength):
            return []

        send_df = read_send_log(send_log_path)
        if send_df.empty:
            return []

        num_devices = extract_device_count_from_tags(tags)
        send_df = normalize_oscillator_id_column(send_df, tags)
        send_df = normalize_time_column(send_df, tags)

        _, cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)
        x, per_percent = compute_per_series(
            send_df=send_df,
            cycle_starts=cycle_starts,
            num_devices=num_devices,
            window_width_cycles=CFG.per_window_width_cycles,
        )
        if len(x) == 0:
            return []

        per_by_cycle = {int(cycle): float(per) for cycle, per in zip(x, per_percent)}
        rows: list[dict] = []
        for target_time_ms in timing_grid_ms():
            target_cycle = cycle_at_target_time(cycle_starts, float(target_time_ms))
            if target_cycle is None:
                continue
            per_value = per_by_cycle.get(int(target_cycle))
            if per_value is None:
                continue
            rows.append(
                {
                    "run_id": run_dir.name,
                    "coupling_function": coupling_function,
                    "coupling_strength": float(coupling_strength),
                    "per_timing_ms": float(target_time_ms),
                    "target_cycle": int(target_cycle),
                    "per_percent": float(per_value),
                }
            )
        return rows
    except Exception:
        return []


def _empty_raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "run_id",
            "coupling_function",
            "coupling_strength",
            "per_timing_ms",
            "target_cycle",
            "per_percent",
        ]
    )


def _empty_aggregated_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "coupling_function",
            "coupling_strength",
            "per_timing_ms",
            "per_percent_mean",
            "per_percent_std",
            "per_percent_min",
            "per_percent_max",
            "target_cycle_mean",
            "count",
        ]
    )


def collect_all_results(results_dir: Path) -> pd.DataFrame:
    run_dirs = sorted([p for p in results_dir.iterdir() if p.is_dir()])
    if not run_dirs:
        return _empty_raw_frame()

    rows: list[dict] = []
    max_workers = min(len(run_dirs), (os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_run, run_dir) for run_dir in run_dirs]

        for future in as_completed(futures):
            rows.extend(future.result())

    if not rows:
        return _empty_raw_frame()

    return pd.DataFrame(rows)


def aggregate_results(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_aggregated_frame()

    return (
        df.groupby(["coupling_function", "coupling_strength", "per_timing_ms"], as_index=False)
        .agg(
            per_percent_mean=("per_percent", "mean"),
            per_percent_std=("per_percent", "std"),
            per_percent_min=("per_percent", "min"),
            per_percent_max=("per_percent", "max"),
            target_cycle_mean=("target_cycle", "mean"),
            count=("per_percent", "size"),
        )
        .sort_values(["coupling_function", "coupling_strength", "per_timing_ms"])
        .reset_index(drop=True)
    )


def get_aggregated_csv_path(output_dir: Path) -> Path:
    start_label = _format_number_for_filename(float(CFG.timing_min_ms))
    end_label = _format_number_for_filename(float(CFG.timing_max_ms))
    step_label = _format_number_for_filename(float(CFG.timing_step_ms))
    return output_dir / (
        f"per_timing_k_heatmap_{start_label}_to_{end_label}ms_"
        f"step_{step_label}_window_{CFG.per_window_width_cycles}.csv"
    )


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
            "per_timing_ms": "float64",
            "per_percent_mean": "float64",
            "per_percent_std": "float64",
            "per_percent_min": "float64",
            "per_percent_max": "float64",
            "target_cycle_mean": "float64",
            "count": "int64",
        },
    ).sort_values(["coupling_function", "coupling_strength", "per_timing_ms"]).reset_index(drop=True)


def _pivot_heatmap(sub: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pivot = sub.pivot_table(
        index="per_timing_ms",
        columns="coupling_strength",
        values="per_percent_mean",
        aggfunc="mean",
    ).sort_index().sort_index(axis=1)

    x = pivot.columns.to_numpy(dtype=np.float64)
    y = pivot.index.to_numpy(dtype=np.float64)
    z = pivot.to_numpy(dtype=np.float64)
    return x, y, z


def per_level_marker_points(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    level: float,
) -> tuple[np.ndarray, np.ndarray]:
    x_points: list[float] = []
    y_points: list[float] = []

    for column_index, coupling_strength in enumerate(x):
        per_values = z[:, column_index]
        matching_indices = np.flatnonzero(np.isfinite(per_values) & (per_values <= level))
        if matching_indices.size == 0:
            continue
        first_index = int(matching_indices[0])
        x_points.append(float(coupling_strength))
        y_points.append(float(y[first_index]))

    return (
        np.array(x_points, dtype=np.float64),
        np.array(y_points, dtype=np.float64),
    )


def draw_per_level_markers(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> None:
    if not CFG.show_per_contour_line:
        return
    if x.size == 0 or y.size == 0:
        return

    level = float(CFG.per_contour_level)
    x_points, y_points = per_level_marker_points(x, y, z, level)
    if x_points.size == 0:
        return

    plt.scatter(
        x_points,
        y_points,
        s=float(getattr(CFG, "per_level_marker_size", 42.0)),
        marker=str(getattr(CFG, "per_level_marker_style", "o")),
        color=CFG.per_contour_color,
        label=f"min timing where PER<={level:g}%",
        zorder=3,
    )
    if CFG.show_per_contour_label:
        plt.legend(fontsize=CFG.per_contour_label_font_size)


def save_plots(df: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    if df.empty:
        return output_paths

    for coupling_function, sub in df.groupby("coupling_function"):
        x, y, z = _pivot_heatmap(sub)
        if x.size == 0 or y.size == 0:
            continue

        plt.figure(figsize=(CFG.figure_width, CFG.figure_height))
        mesh = plt.imshow(
            z,
            aspect="auto",
            origin="lower",
            interpolation="nearest",
            cmap=CFG.colormap,
            vmin=CFG.color_min,
            vmax=CFG.color_max,
            extent=[float(x.min()), float(x.max()), float(y.min()), float(y.max())],
        )
        draw_per_level_markers(x, y, z)

        colorbar = plt.colorbar(mesh)
        colorbar.set_label(CFG.colorbar_label, fontsize=CFG.font_size_label)
        colorbar.ax.tick_params(labelsize=CFG.font_size_ticks)

        if CFG.xlim_min is not None or CFG.xlim_max is not None:
            plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

        if CFG.ylim_min is not None or CFG.ylim_max is not None:
            plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

        plt.xlabel(coupling_strength_axis_label(CFG.x_label), fontsize=CFG.font_size_label)
        plt.ylabel(CFG.y_label, fontsize=CFG.font_size_label)

        if CFG.show_title:
            display_name = _display_coupling_function(str(coupling_function))
            plt.title(
                f"PER heatmap\nmethod={display_name}, window={CFG.per_window_width_cycles} cycles",
                fontsize=CFG.font_size_title,
            )

        plt.xticks(fontsize=CFG.font_size_ticks)
        plt.yticks(fontsize=CFG.font_size_ticks)
        plt.tight_layout()

        safe_function = _safe_filename_part(str(coupling_function))
        output_path = output_dir / f"{safe_function}_per_timing_k_heatmap.pdf"
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
        print("no PER timing-K heatmaps were generated")
        return

    for path in plot_paths:
        print(f"saved: {path}")


if __name__ == "__main__":
    main()
