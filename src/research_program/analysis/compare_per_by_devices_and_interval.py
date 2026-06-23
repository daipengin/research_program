from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import matplotlib.colors as mcolors

from research_program.config.plot_config import COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG
from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run
from research_program.io.send_log import (
    add_detection_time_column,
    detection_time_values,
    normalize_send_time_columns,
)


CFG = COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG


def read_metadata(metadata_path: Path) -> tuple[float, list[str], str]:
    df = pd.read_csv(metadata_path)

    cycle_time = float(df.loc[0, "cycle_time"])
    raw_coupling_function = df.loc[0, "coupling_function"] if "coupling_function" in df.columns else None

    if pd.isna(raw_coupling_function) or raw_coupling_function is None or str(raw_coupling_function).strip() == "":
        coupling_function = "None"
    else:
        coupling_function = str(raw_coupling_function).strip()

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


def extract_per_at_target_cycle(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    num_devices: int,
    target_cycle: int,
    window_width_cycles: int,
) -> Optional[float]:
    x, per_percent = compute_per_series(
        send_df=send_df,
        cycle_starts=cycle_starts,
        num_devices=num_devices,
        window_width_cycles=window_width_cycles,
    )

    if len(x) == 0:
        return None

    mask = x == target_cycle
    if not np.any(mask):
        return None

    return float(per_percent[mask][0])


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

    send_interval, tags, coupling_function = read_metadata(metadata_path)
    num_devices = extract_device_count_from_tags(tags)

    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)

    _, cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)

    per_value = extract_per_at_target_cycle(
        send_df=send_df,
        cycle_starts=cycle_starts,
        num_devices=num_devices,
        target_cycle=CFG.target_cycle,
        window_width_cycles=CFG.per_window_width_cycles,
    )

    if per_value is None:
        return None

    return {
        "run_id": run_dir.name,
        "coupling_function": coupling_function,
        "num_devices": num_devices,
        "send_interval": send_interval,
        "per_percent": per_value,
    }


def collect_all_results(results_dir: Path) -> pd.DataFrame:
    run_dirs = sorted([p for p in results_dir.iterdir() if p.is_dir()])

    if not run_dirs:
        return pd.DataFrame(
            columns=["run_id", "coupling_function", "num_devices", "send_interval", "per_percent"]
        )

    rows = []

    max_workers = min(len(run_dirs), (os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_run, run_dir) for run_dir in run_dirs]

        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                rows.append(result)

    if not rows:
        return pd.DataFrame(
            columns=["run_id", "coupling_function", "num_devices", "send_interval", "per_percent"]
        )

    return pd.DataFrame(rows)


def aggregate_results(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=["coupling_function", "num_devices", "send_interval", "per_percent_mean", "count"]
        )

    grouped = (
        df.groupby(["coupling_function", "num_devices", "send_interval"], as_index=False)
        .agg(
            per_percent_mean=("per_percent", "mean"),
            count=("per_percent", "size"),
        )
        .sort_values(["coupling_function", "send_interval", "num_devices"])
        .reset_index(drop=True)
    )
    return grouped

def get_aggregated_csv_path(output_dir: Path) -> Path:
    return output_dir / f"per_compare_cycle_{CFG.target_cycle}.csv"


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
            "num_devices": "int64",
            "send_interval": "float64",
            "per_percent_mean": "float64",
            "count": "int64",
        },
    ).sort_values(
        ["coupling_function", "send_interval", "num_devices"]
    ).reset_index(drop=True)



def format_send_interval_label(send_interval_ms: float) -> str:
    """
    送信間隔を秒表示に変換する。
    例:
        10000 ms -> 10 s
        30000 ms -> 30 s
        12500 ms -> 12.5 s
    """
    sec = send_interval_ms / 1000.0

    if float(sec).is_integer():
        return f"{int(sec)} s"

    return f"{sec:g} s"


def mix_with_white(color: str, white_mix: float) -> tuple[float, float, float]:
    """
    color を white_mix の割合だけ白に近づけた RGB を返す。
    white_mix=0 なら元の色，1 なら白。
    """
    rgb = np.array(mcolors.to_rgb(color), dtype=float)
    white = np.array([1.0, 1.0, 1.0], dtype=float)
    mixed = (1.0 - white_mix) * rgb + white_mix * white
    mixed = np.clip(mixed, 0.0, 1.0)
    return tuple(mixed.tolist())

def get_color_for_send_interval(
    base_color: str,
    send_interval: float,
    interval_min: float,
    interval_max: float,
) -> tuple[float, float, float]:
    """
    同じ手法内で，send_interval の違いを色の濃さで表す。
    """
    if interval_max == interval_min:
        white_mix = CFG.max_interval_white_mix
    else:
        normalized = (send_interval - interval_min) / (interval_max - interval_min)
        white_mix = (
            CFG.min_interval_white_mix
            + (CFG.max_interval_white_mix - CFG.min_interval_white_mix) * normalized
        )

    return mix_with_white(base_color, white_mix)



def add_sample_watermark() -> None:
    if not CFG.show_sample_watermark:
        return

    plt.figtext(
        0.5,
        0.5,
        CFG.sample_watermark_text,
        ha="center",
        va="center",
        fontsize=CFG.sample_watermark_font_size,
        alpha=CFG.sample_watermark_alpha,
        rotation=CFG.sample_watermark_rotation,
    )



def is_target_method(coupling_function: str) -> bool:
    if not CFG.target_coupling_functions:
        return True
    return coupling_function in CFG.target_coupling_functions



def save_plots(df: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []

    if df.empty:
        return output_paths

    for coupling_function, sub in df.groupby("coupling_function"):
        sub = sub.sort_values(["send_interval", "num_devices"]).reset_index(drop=True)

        plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

        send_intervals_sorted = sorted(sub["send_interval"].unique().tolist())

        base_color = CFG.coupling_function_base_colors.get(coupling_function, "tab:blue")
        interval_min = min(send_intervals_sorted)
        interval_max = max(send_intervals_sorted)

        for idx, send_interval in enumerate(send_intervals_sorted):
            sub2 = sub[sub["send_interval"] == send_interval].sort_values("num_devices")

            line_style = CFG.line_styles[idx % len(CFG.line_styles)]
            marker_style = CFG.marker_styles[idx % len(CFG.marker_styles)]
            label = format_send_interval_label(send_interval)

            plot_color = get_color_for_send_interval(
                base_color=base_color,
                send_interval=send_interval,
                interval_min=interval_min,
                interval_max=interval_max,
            )

            plt.plot(
                sub2["num_devices"],
                sub2["per_percent_mean"],
                linestyle=line_style,
                marker=marker_style,
                color=plot_color,
                markersize=CFG.marker_size,
                linewidth=CFG.line_width,
                label=label,
            )

        if CFG.xlim_min is not None or CFG.xlim_max is not None:
            plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

        if CFG.ylim_min is not None or CFG.ylim_max is not None:
            plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

        plt.xlabel("Number of devices", fontsize=CFG.font_size_label)
        plt.ylabel("PER [%]", fontsize=CFG.font_size_label)

        display_name = "FrogChorus" if coupling_function == "FROGCHORUS" else coupling_function
        if CFG.show_title:
            plt.title(
                f"PER at cycle {CFG.target_cycle}\nmethod={display_name}",
                fontsize=CFG.font_size_title,
            )

        plt.xticks(fontsize=CFG.font_size_ticks)
        plt.yticks(fontsize=CFG.font_size_ticks)
        plt.grid(True)
        plt.legend(title="Send interval [s]", fontsize=CFG.font_size_legend)
        add_sample_watermark()
        plt.tight_layout()

        output_path = output_dir / f"{coupling_function}_cycle_{CFG.target_cycle}.pdf"
        plt.savefig(output_path, dpi=CFG.save_dpi)
        plt.close()

        output_paths.append(output_path)

    return output_paths

def save_combined_method_plot(df: pd.DataFrame, output_dir: Path) -> Optional[Path]:
    if df.empty:
        return None

    target_df = df[df["coupling_function"].map(is_target_method)].copy()
    if target_df.empty:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"combined_methods_cycle_{CFG.target_cycle}.pdf"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

    methods = sorted(target_df["coupling_function"].unique().tolist())
    all_intervals = sorted(target_df["send_interval"].unique().tolist())

    



    line_styles = list(CFG.line_styles)
    marker_styles = list(CFG.marker_styles)

    # 同じ送信間隔なら同じ色
    cmap = plt.get_cmap("tab10")
    interval_color_map = {
        send_interval: cmap(i % 10)
        for i, send_interval in enumerate(all_intervals)
    }

    for method_idx, coupling_function in enumerate(methods):
        sub_method = target_df[target_df["coupling_function"] == coupling_function].copy()
        send_intervals_sorted = sorted(sub_method["send_interval"].unique().tolist())

        for interval_idx, send_interval in enumerate(send_intervals_sorted):
            sub2 = sub_method[sub_method["send_interval"] == send_interval].sort_values("num_devices")

            """
            line_style = line_styles[method_idx % len(line_styles)]
            marker_style = marker_styles[method_idx % len(marker_styles)]

            base_color = CFG.coupling_function_base_colors.get(coupling_function, "tab:blue")
            interval_min = min(send_intervals_sorted)
            interval_max = max(send_intervals_sorted)

            color = get_color_for_send_interval(
                base_color=base_color,
                send_interval=send_interval,
                interval_min=interval_min,
                interval_max=interval_max,
            )

            display_name = "FrogChorus" if coupling_function == "FROGCHORUS" else coupling_function
            label = f"{display_name}, {format_send_interval_label(send_interval)}"
            """

            line_style = line_styles[method_idx % len(line_styles)]
            marker_style = marker_styles[method_idx % len(marker_styles)]
            color = interval_color_map[send_interval]

            display_name = "FrogChorus" if coupling_function == "LINEAR" else coupling_function

            label = f"{display_name}, {format_send_interval_label(send_interval)}"

            plt.plot(
                sub2["num_devices"],
                sub2["per_percent_mean"],
                linestyle=line_style,
                marker=marker_style,
                color=color,
                markersize=CFG.marker_size,
                linewidth=CFG.line_width,
                label=label,
            )

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.ylim_min is not None or CFG.ylim_max is not None:
        plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

    plt.xlabel("Number of devices", fontsize=CFG.font_size_label)
    plt.ylabel("PER [%]", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"PER at cycle {CFG.target_cycle}\nselected methods comparison",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)
    plt.grid(True)
    plt.legend(fontsize=CFG.font_size_legend)
    add_sample_watermark()
    plt.tight_layout()

    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()

    return output_path



def main() -> None:
    results_dir = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", CFG.results_dir))
    force_recalculate = os.environ.get("RESEARCH_PROGRAM_FORCE_RECALCULATE") == "1"
    if not results_dir.exists():
        raise FileNotFoundError(f"results folder not found: {results_dir}")

    CFG.graphs_dir.mkdir(parents=True, exist_ok=True)

    aggregated_csv_path = get_aggregated_csv_path(CFG.graphs_dir)

    if CFG.use_existing_csv_if_available and aggregated_csv_path.exists() and not force_recalculate:
        agg_df = read_aggregated_csv(aggregated_csv_path)
        print(f"loaded existing csv: {aggregated_csv_path}")
    else:
        raw_df = collect_all_results(results_dir)
        agg_df = aggregate_results(raw_df)

        csv_path = save_aggregated_csv(agg_df, CFG.graphs_dir)
        print(f"saved: {csv_path}")

    plot_paths = save_plots(agg_df, CFG.graphs_dir)
    for path in plot_paths:
        print(f"saved: {path}")

    if CFG.show_combined_method_plot:
        combined_path = save_combined_method_plot(agg_df, CFG.graphs_dir)
        if combined_path is not None:
            print(f"saved: {combined_path}")


if __name__ == "__main__":
    main()
