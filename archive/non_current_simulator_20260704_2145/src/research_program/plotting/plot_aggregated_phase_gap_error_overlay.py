from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from research_program.config.plot_config import AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG


CFG = AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG


def parse_filename(csv_path: Path) -> tuple[str, int]:
    """
    aggregated_stats/KURAMOTO_3.csv のような名前から
    coupling_function と coupling_strength を取り出す。
    """
    stem = csv_path.stem
    m = re.fullmatch(r"(.+)_(\d+)", stem)
    if m is None:
        raise ValueError(f"unexpected filename format: {csv_path.name}")

    coupling_function = m.group(1)
    coupling_strength = int(m.group(2))
    return coupling_function, coupling_strength


def apply_strength_rule(coupling_function: str, coupling_strength: int) -> bool:
    """
    coupling_function ごとの個別ルールを適用する。
    ルールが無ければ，グローバル設定を使う。
    """
    rule = CFG.coupling_function_strength_rules.get(coupling_function)

    if rule is not None:
        if rule.strength_min is not None and coupling_strength < rule.strength_min:
            return False

        if rule.strength_max is not None and coupling_strength > rule.strength_max:
            return False

        if rule.step is not None:
            base = rule.strength_min if rule.strength_min is not None else coupling_strength
            if (coupling_strength - base) % rule.step != 0:
                return False

        return True

    if CFG.coupling_strength_min is not None:
        if coupling_strength < CFG.coupling_strength_min:
            return False

    if CFG.coupling_strength_max is not None:
        if coupling_strength > CFG.coupling_strength_max:
            return False

    if hasattr(CFG, "coupling_strength_step") and CFG.coupling_strength_step is not None:
        base = CFG.coupling_strength_min if CFG.coupling_strength_min is not None else coupling_strength
        if (coupling_strength - base) % CFG.coupling_strength_step != 0:
            return False

    return True


def is_target_group(coupling_function: str, coupling_strength: int) -> bool:
    if CFG.target_coupling_functions:
        if coupling_function not in CFG.target_coupling_functions:
            return False

    return apply_strength_rule(coupling_function, coupling_strength)


def read_aggregated_stats(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path,
        dtype={
            "cycle_index": "int64",
            "count": "float64",
            "mean": "float64",
            "min": "float64",
            "max": "float64",
            "median": "float64",
            "std": "float64",
            "q25": "float64",
            "q75": "float64",
        },
    )
    return df.sort_values("cycle_index").reset_index(drop=True)


def collect_target_csv_files(aggregated_stats_dir: Path) -> list[tuple[Path, str, int]]:
    csv_files = sorted(aggregated_stats_dir.glob("*.csv"))
    targets: list[tuple[Path, str, int]] = []

    for csv_path in csv_files:
        coupling_function, coupling_strength = parse_filename(csv_path)
        if is_target_group(coupling_function, coupling_strength):
            targets.append((csv_path, coupling_function, coupling_strength))

    return targets


def build_function_style_maps(
    targets: list[tuple[Path, str, int]],
) -> tuple[dict[str, str], dict[str, str]]:
    unique_functions = sorted({coupling_function for _, coupling_function, _ in targets})

    default_colors = [
        "tab:blue",
        "tab:orange",
        "tab:green",
        "tab:red",
        "tab:purple",
        "tab:brown",
        "tab:pink",
        "tab:gray",
        "tab:olive",
        "tab:cyan",
    ]
    default_line_styles = ["-", "--", "-.", ":"]

    color_map: dict[str, str] = {}
    line_style_map: dict[str, str] = {}

    for i, coupling_function in enumerate(unique_functions):
        color_map[coupling_function] = CFG.coupling_function_base_colors.get(
            coupling_function,
            default_colors[i % len(default_colors)],
        )
        line_style_map[coupling_function] = CFG.coupling_function_line_styles.get(
            coupling_function,
            default_line_styles[i % len(default_line_styles)],
        )

    return color_map, line_style_map


def compute_strength_range(targets: list[tuple[Path, str, int]]) -> tuple[int, int]:
    strengths = [coupling_strength for _, _, coupling_strength in targets]
    return min(strengths), max(strengths)


def mix_with_white(color: str, white_mix: float) -> tuple[float, float, float]:
    """
    color を white_mix の割合だけ白に近づけた RGB を返す。
    white_mix=0 なら元の色，white_mix=1 なら白。
    """
    rgb = np.array(mcolors.to_rgb(color), dtype=float)
    white = np.array([1.0, 1.0, 1.0], dtype=float)
    mixed = (1.0 - white_mix) * rgb + white_mix * white
    mixed = np.clip(mixed, 0.0, 1.0)
    return tuple(mixed.tolist())


def get_color_for_strength(
    base_color: str,
    coupling_strength: int,
    strength_min: int,
    strength_max: int,
) -> tuple[float, float, float]:
    if strength_max == strength_min:
        white_mix = CFG.max_strength_white_mix
    else:
        normalized = (coupling_strength - strength_min) / (strength_max - strength_min)
        white_mix = (
            CFG.min_strength_white_mix
            + (CFG.max_strength_white_mix - CFG.min_strength_white_mix) * normalized
        )

    return mix_with_white(base_color, white_mix)


def find_convergence_point(
    df: pd.DataFrame,
    window_cycles: int,
    threshold: float,
) -> tuple[Optional[int], Optional[float]]:
    mean_series = df[["cycle_index", "mean"]].dropna().reset_index(drop=True)

    if len(mean_series) < window_cycles:
        return None, None

    values = mean_series["mean"].to_numpy()
    cycles = mean_series["cycle_index"].to_numpy()

    for i in range(len(values) - window_cycles + 1):
        window = values[i:i + window_cycles]
        if window.max() - window.min() <= threshold:
            return int(cycles[i]), float(values[i])

    return None, None


def choose_visible_anchor_position(
    x: np.ndarray,
    y: np.ndarray,
    xlim_min: float | None,
    xlim_max: float | None,
    position: str,
) -> tuple[float, float] | None:
    """
    実際に表示される x 範囲内にある点から，
    吹き出しの基準点を選ぶ。
    """
    if len(x) == 0 or len(y) == 0:
        return None

    x_min_data = float(np.min(x))
    x_max_data = float(np.max(x))

    visible_min = xlim_min if xlim_min is not None else x_min_data
    visible_max = xlim_max if xlim_max is not None else x_max_data

    visible_mask = (x >= visible_min) & (x <= visible_max)

    if not np.any(visible_mask):
        return None

    x_vis = x[visible_mask]
    y_vis = y[visible_mask]

    if position == "end":
        return float(x_vis[-1]), float(y_vis[-1])

    mid_idx = len(x_vis) // 2
    return float(x_vis[mid_idx]), float(y_vis[mid_idx])


def generate_callout_candidates(
    anchor_x: float,
    anchor_y: float,
    x_span: float,
    y_span: float,
) -> list[tuple[float, float, str, str]]:
    """
    吹き出しテキストの候補位置を返す。
    戻り値: (text_x, text_y, ha, va)
    """
    dx_small = 0.03 * x_span if x_span > 0 else 1.0
    dx_large = 0.06 * x_span if x_span > 0 else 2.0
    dy_small = 0.04 * y_span if y_span > 0 else 0.02
    dy_large = 0.08 * y_span if y_span > 0 else 0.04

    return [
        (anchor_x + dx_large, anchor_y + dy_large, "left", "bottom"),
        (anchor_x + dx_large, anchor_y - dy_large, "left", "top"),
        (anchor_x - dx_large, anchor_y + dy_large, "right", "bottom"),
        (anchor_x - dx_large, anchor_y - dy_large, "right", "top"),
        (anchor_x + dx_large, anchor_y, "left", "center"),
        (anchor_x - dx_large, anchor_y, "right", "center"),
        (anchor_x, anchor_y + dy_large, "center", "bottom"),
        (anchor_x, anchor_y - dy_large, "center", "top"),
        (anchor_x + dx_small, anchor_y + dy_small, "left", "bottom"),
        (anchor_x - dx_small, anchor_y + dy_small, "right", "bottom"),
        (anchor_x + dx_small, anchor_y - dy_small, "left", "top"),
        (anchor_x - dx_small, anchor_y - dy_small, "right", "top"),
    ]


def boxes_overlap(box1: tuple[float, float, float, float], box2: tuple[float, float, float, float]) -> bool:
    """
    box = (x0, y0, x1, y1)
    """
    return not (
        box1[2] < box2[0]
        or box1[0] > box2[2]
        or box1[3] < box2[1]
        or box1[1] > box2[3]
    )


def make_text_box(
    text_x: float,
    text_y: float,
    x_span: float,
    y_span: float,
    strength_text: str,
) -> tuple[float, float, float, float]:
    """
    データ座標上での簡易テキストボックスを返す。
    """
    char_count = max(len(strength_text), 1)
    box_w = max(0.02 * x_span * char_count, 0.02 * x_span) if x_span > 0 else 1.0
    box_h = 0.06 * y_span if y_span > 0 else 0.03

    return (
        text_x - box_w / 2.0,
        text_y - box_h / 2.0,
        text_x + box_w / 2.0,
        text_y + box_h / 2.0,
    )


def save_overlay_plot(
    targets: list[tuple[Path, str, int]],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / CFG.output_filename

    color_map, line_style_map = build_function_style_maps(targets)

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))
    used_label_boxes: list[tuple[float, float, float, float]] = []

    plotted_count = 0
    targets_sorted = sorted(targets, key=lambda x: (x[1], x[2]))

    strength_min, strength_max = compute_strength_range(targets)

    legend_handles = []
    display_name_map = {
        "KURAMOTO": "Kuramoto",
        "LINEAR": "FrogChorus",
    }

    for coupling_function in sorted(color_map.keys()):
        base_color = color_map[coupling_function]
        line_style = line_style_map[coupling_function]
        label = display_name_map.get(coupling_function, coupling_function)

        legend_handles.append(
            Line2D(
                [0],
                [0],
                color=base_color,
                linestyle=line_style,
                linewidth=CFG.linewidth,
                label=label,
            )
        )

    # 先に線を全部描く
    line_draw_data: list[dict[str, object]] = []

    for csv_path, coupling_function, coupling_strength in targets_sorted:
        df = read_aggregated_stats(csv_path)
        if df.empty or "mean" not in df.columns:
            continue

        valid = ~df["mean"].isna()
        if not valid.any():
            continue

        x = df.loc[valid, "cycle_index"]
        y = df.loc[valid, "mean"]

        x_vals = np.asarray(x, dtype=float)
        y_vals = np.asarray(y, dtype=float)

        if len(x_vals) == 0 or len(y_vals) == 0:
            continue

        base_color = color_map[coupling_function]
        line_style = line_style_map[coupling_function]
        plot_color = get_color_for_strength(
            base_color=base_color,
            coupling_strength=coupling_strength,
            strength_min=strength_min,
            strength_max=strength_max,
        )

        plt.plot(
            x_vals,
            y_vals,
            linewidth=CFG.linewidth,
            linestyle=line_style,
            color=plot_color,
        )

        if CFG.show_convergence_marker:
            conv_cycle, conv_value = find_convergence_point(
                df=df,
                window_cycles=CFG.convergence_window_cycles,
                threshold=CFG.convergence_threshold,
            )

            if conv_cycle is not None and conv_value is not None:
                plt.scatter(
                    [conv_cycle],
                    [conv_value],
                    s=CFG.convergence_marker_size,
                    marker=CFG.convergence_marker_style,
                    color=plot_color,
                    edgecolors="black",
                    linewidths=0.8,
                    zorder=5,
                )

        line_draw_data.append(
            {
                "coupling_strength": coupling_strength,
                "x_vals": x_vals,
                "y_vals": y_vals,
            }
        )

        plotted_count += 1

    if plotted_count == 0:
        raise ValueError("no valid target data to plot")

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.ylim_min is not None or CFG.ylim_max is not None:
        plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

    # 線を描いた後で吹き出しラベルを配置する
    if CFG.show_strength_label:
        xlim_current = plt.xlim()
        ylim_current = plt.ylim()

        x_span_plot = float(xlim_current[1] - xlim_current[0])
        y_span_plot = float(ylim_current[1] - ylim_current[0])

        for item in line_draw_data:
            coupling_strength = int(item["coupling_strength"])
            x_vals = np.asarray(item["x_vals"], dtype=float)
            y_vals = np.asarray(item["y_vals"], dtype=float)

            anchor = choose_visible_anchor_position(
                x=x_vals,
                y=y_vals,
                xlim_min=xlim_current[0],
                xlim_max=xlim_current[1],
                position=CFG.strength_label_position,
            )

            if anchor is None:
                continue

            anchor_x, anchor_y = anchor
            candidates = generate_callout_candidates(
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                x_span=x_span_plot,
                y_span=y_span_plot,
            )

            strength_text = f"{coupling_strength * 0.0001:g}"
            chosen = None

            for text_x, text_y, ha, va in candidates:
                # 表示範囲外すぎる候補は除外
                if text_x < xlim_current[0] or text_x > xlim_current[1]:
                    continue
                if text_y < ylim_current[0] or text_y > ylim_current[1]:
                    continue

                cand_box = make_text_box(
                    text_x=text_x,
                    text_y=text_y,
                    x_span=x_span_plot,
                    y_span=y_span_plot,
                    strength_text=strength_text,
                )

                overlapped = any(boxes_overlap(cand_box, used_box) for used_box in used_label_boxes)
                if not overlapped:
                    chosen = (text_x, text_y, ha, va, cand_box)
                    break

            if chosen is None:
                # 全部重なるなら最初の候補を採用
                text_x, text_y, ha, va = candidates[0]
                cand_box = make_text_box(
                    text_x=text_x,
                    text_y=text_y,
                    x_span=x_span_plot,
                    y_span=y_span_plot,
                    strength_text=strength_text,
                )
                chosen = (text_x, text_y, ha, va, cand_box)

            text_x, text_y, ha, va, cand_box = chosen

            plt.annotate(
                strength_text,
                xy=(anchor_x, anchor_y),
                xytext=(text_x, text_y),
                textcoords="data",
                ha=ha,
                va=va,
                fontsize=CFG.strength_label_font_size,
                color="black",
                zorder=10,
                bbox=dict(
                    boxstyle="round,pad=0.2",
                    facecolor="white",
                    edgecolor="black",
                    alpha=0.8,
                    linewidth=0.6,
                ),
                arrowprops=dict(
                    arrowstyle="-",
                    color="black",
                    linewidth=0.8,
                    shrinkA=0,
                    shrinkB=0,
                    alpha=0.8,
                ),
                annotation_clip=True,
            )

            used_label_boxes.append(cand_box)

    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel("Mean phase gap error ratio", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            "Overlay of Mean Phase Gap Error Ratio",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)
    plt.grid(True)

    if CFG.show_legend:
        plt.legend(handles=legend_handles, fontsize=CFG.font_size_legend)

    plt.subplots_adjust(left=0.12, right=0.88, bottom=0.12, top=0.90)
    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()

    return output_path


def main() -> None:
    aggregated_stats_dir = Path(os.environ.get("RESEARCH_PROGRAM_AGGREGATED_DIR", CFG.aggregated_stats_dir))
    graphs_dir = CFG.graphs_dir
    graphs_dir.mkdir(parents=True, exist_ok=True)

    if not aggregated_stats_dir.exists():
        raise FileNotFoundError(f"aggregated_stats folder not found: {aggregated_stats_dir}")

    targets = collect_target_csv_files(aggregated_stats_dir)

    if not targets:
        print("no target aggregated csv files found")
        return

    output_path = save_overlay_plot(targets, graphs_dir)
    print(f"saved: {output_path}")


if __name__ == "__main__":
    main()
