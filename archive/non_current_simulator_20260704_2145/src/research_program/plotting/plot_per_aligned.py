from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.config.plot_config import PER_ALIGNED_PLOT_CONFIG
from research_program.io.send_log import (
    add_detection_time_column,
    detection_time_values,
    normalize_send_time_columns,
)
from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run


CFG = PER_ALIGNED_PLOT_CONFIG


def read_metadata(metadata_path: Path) -> tuple[float, list[str], str]:
    df = pd.read_csv(metadata_path)

    cycle_time = float(df.loc[0, "cycle_time"])
    coupling_function = str(df.loc[0, "coupling_function"])

    tags_raw = df.loc[0, "tags"] if "tags" in df.columns else ""
    if pd.isna(tags_raw) or tags_raw == "":
        tags = []
    else:
        tags = [tag.strip() for tag in str(tags_raw).split(";") if tag.strip()]

    if "sec" in tags:
        cycle_time *= 1000.0

    return cycle_time, tags, coupling_function


def extract_device_count_from_tags(tags: list[str]) -> int:
    for tag in tags:
        m = re.fullmatch(r"(\d+)dai", tag)
        if m is not None:
            return int(m.group(1))
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

    def parse_id(x) -> int:
        s = str(x).strip()

        if "hex" in tags:
            return int(s, 16)

        try:
            return int(s)
        except ValueError:
            return int(s, 16)

    send_df["oscillator_id"] = send_df["oscillator_id"].map(parse_id)
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
    valid = np.isfinite(times) & (cycle_index > 0)

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
        counts_full[cycle_indices - 1] = counts_by_cycle.to_numpy(dtype=np.int64)

    kernel = np.ones(window_width_cycles, dtype=np.int64)
    window_counts = np.convolve(counts_full, kernel, mode="valid")

    denominator = float(num_devices * window_width_cycles)
    success_ratio = window_counts / denominator
    per_percent = (1.0 - success_ratio) * 100.0
    per_percent = np.clip(per_percent, 0.0, None)

    x = np.arange(window_width_cycles, max_cycle + 1, dtype=np.int64)
    return x, per_percent


def compute_per_change_series(
    x: np.ndarray,
    per_percent: np.ndarray,
    change_width_cycles: int,
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) == 0 or len(per_percent) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)

    x_to_per = {int(cx): float(py) for cx, py in zip(x, per_percent)}

    x_change_list = []
    per_change_list = []

    for cx in x:
        prev_cycle = int(cx) - change_width_cycles
        if prev_cycle in x_to_per:
            x_change_list.append(int(cx))
            per_change_list.append(float(x_to_per[int(cx)] - x_to_per[prev_cycle]))

    return np.array(x_change_list, dtype=np.int64), np.array(per_change_list, dtype=np.float64)


def choose_base_cycle(
    run_id: str,
    x: np.ndarray,
    per_percent: np.ndarray,
) -> Optional[int]:
    # まず run_id ごとの直接指定を優先
    if run_id in CFG.per_aligned_base_cycle_by_run_id:
        return int(CFG.per_aligned_base_cycle_by_run_id[run_id])

    if len(x) == 0 or len(per_percent) == 0:
        return None

    mode = CFG.base_cycle_mode

    if mode == "fixed":
        return int(CFG.fixed_base_cycle)

    if mode == "first_available":
        return int(x[0])

    if mode == "best_per":
        idx = int(np.argmin(per_percent))
        return int(x[idx])

    if mode == "largest_improvement":
        x_change, per_change = compute_per_change_series(
            x=x,
            per_percent=per_percent,
            change_width_cycles=CFG.per_change_width_cycles,
        )
        if len(x_change) == 0:
            return None
        idx = int(np.argmin(per_change))
        return int(x_change[idx])

    raise ValueError(f"Unknown base_cycle_mode: {mode}")

def get_legend_name(run_id: str) -> str:
    return CFG.legend_name_by_run_id.get(run_id, run_id)


def aligned_result_to_frame(result: dict) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": result["run_id"],
            "coupling_function": result["coupling_function"],
            "base_cycle": int(result["base_cycle"]),
            "cycle_index": result["x"].astype(np.int64),
            "per_percent": result["per_percent"].astype(np.float64),
            "aligned_cycle": result["aligned_x"].astype(np.int64),
            "aligned_per_percent": result["aligned_per"].astype(np.float64),
        }
    )


def overlay_results_to_frame(results: list[dict]) -> pd.DataFrame:
    frames = [
        aligned_result_to_frame(result)
        for result in sorted(results, key=lambda r: (r["coupling_function"], r["run_id"]))
    ]
    if not frames:
        return pd.DataFrame(
            columns=[
                "run_id",
                "coupling_function",
                "base_cycle",
                "cycle_index",
                "per_percent",
                "aligned_cycle",
                "aligned_per_percent",
            ]
        )
    return pd.concat(frames, ignore_index=True)


def save_graph_data_csv(path: Path, df: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path



def build_aligned_per_series(
    x: np.ndarray,
    per_percent: np.ndarray,
    base_cycle: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    base_cycle を 0 に揃えた横軸を作る。
    基準前のデータも負の値として残す。
    """
    aligned_x = x - base_cycle
    return aligned_x.astype(np.int64), per_percent.astype(np.float64)


def process_run(run_dir: Path) -> Optional[dict]:
    send_log_path = run_dir / "send_log.csv"
    metadata_path = run_dir / "metadata.csv"
    cycle_data_path = run_dir / "calculated_Cycle_data.csv"

    if not send_log_path.exists() or not metadata_path.exists():
        return None

    if not cycle_data_path.exists():
        ensure_cycle_data_for_run(run_dir)

    if not cycle_data_path.exists():
        return None

    send_df = read_send_log(send_log_path)
    if send_df.empty:
        return None

    cycle_time, tags, coupling_function = read_metadata(metadata_path)
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
        return None

    base_cycle = choose_base_cycle(
        run_id=run_dir.name,
        x=x,
        per_percent=per_percent,
    )
    if base_cycle is None:
        return None

    aligned_x, aligned_per = build_aligned_per_series(
        x=x,
        per_percent=per_percent,
        base_cycle=base_cycle,
    )

    return {
        "run_id": run_dir.name,
        "coupling_function": coupling_function,
        "base_cycle": base_cycle,
        "x": x,
        "per_percent": per_percent,
        "aligned_x": aligned_x,
        "aligned_per": aligned_per,
    }


def collect_all_results(results_dir: Path) -> list[dict]:
    run_dirs = sorted([p for p in results_dir.iterdir() if p.is_dir()])

    if not run_dirs:
        return []

    rows: list[dict] = []

    max_workers = min(len(run_dirs), (os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_run, run_dir) for run_dir in run_dirs]

        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                rows.append(result)

    return rows


def save_individual_plots(results: list[dict], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []

    for result in results:
        run_id = result["run_id"]
        base_cycle = result["base_cycle"]
        aligned_x = result["aligned_x"]
        aligned_per = result["aligned_per"]

        plt.figure(figsize=(CFG.figure_width, CFG.figure_height))
        plt.plot(
            aligned_x,
            aligned_per,
            linewidth=CFG.line_width,
        )
        plt.axvline(0, linewidth=1)

        if CFG.xlim_min is not None or CFG.xlim_max is not None:
            plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

        if CFG.ylim_min is not None or CFG.ylim_max is not None:
            plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

        plt.xlabel("Aligned cycle number", fontsize=CFG.font_size_label)
        plt.ylabel("PER [%]", fontsize=CFG.font_size_label)

        if CFG.show_title:
            plt.title(
                f"Aligned PER\nrun={run_id}, base_cycle={base_cycle}",
                fontsize=CFG.font_size_title,
            )

        plt.xticks(fontsize=CFG.font_size_ticks)
        plt.yticks(fontsize=CFG.font_size_ticks)
        plt.grid(True)
        plt.tight_layout()

        output_path = output_dir / f"{run_id}_aligned.pdf"
        plt.savefig(output_path, dpi=CFG.save_dpi)
        plt.close()
        save_graph_data_csv(output_path.with_suffix(".csv"), aligned_result_to_frame(result))

        output_paths.append(output_path)

    return output_paths


def save_overlay_plot(results: list[dict], output_dir: Path) -> Optional[Path]:
    if not results:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "overlay_aligned_per.pdf"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

    for result in sorted(results, key=lambda r: (r["coupling_function"], r["run_id"])):
        aligned_x = result["aligned_x"]
        aligned_per = result["aligned_per"]
        label = get_legend_name(result["run_id"])

        plt.plot(
            aligned_x,
            aligned_per,
            linewidth=CFG.line_width,
            label=label,
        )

    plt.axvline(0, linewidth=1)

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.ylim_min is not None or CFG.ylim_max is not None:
        plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel("PER [%]", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"Overlay of aligned PER\nbase_cycle_mode={CFG.base_cycle_mode}",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)
    plt.grid(True)
    if CFG.legend_bbox_to_anchor is None:
        plt.legend(
            fontsize=CFG.font_size_legend,
            loc=CFG.legend_loc,
        )
    else:
        plt.legend(
            fontsize=CFG.font_size_legend,
            loc=CFG.legend_loc,
            bbox_to_anchor=CFG.legend_bbox_to_anchor,
        )
    plt.tight_layout()

    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()
    save_graph_data_csv(output_path.with_suffix(".csv"), overlay_results_to_frame(results))

    return output_path


def main() -> None:
    results_dir = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", CFG.results_dir))
    if not results_dir.exists():
        raise FileNotFoundError(f"results folder not found: {results_dir}")

    CFG.graphs_dir.mkdir(parents=True, exist_ok=True)

    results = collect_all_results(results_dir)

    if CFG.save_individual_plots:
        paths = save_individual_plots(results, CFG.graphs_dir)
        for path in paths:
            print(f"saved: {path}")

    if CFG.save_overlay_plot:
        overlay_path = save_overlay_plot(results, CFG.graphs_dir)
        if overlay_path is not None:
            print(f"saved: {overlay_path}")


if __name__ == "__main__":
    main()
