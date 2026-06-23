from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Optional


YRangeMode = Literal["0_to_2pi", "minus_pi_to_pi"]


def _plot_overrides() -> dict[str, dict[str, object]]:
    raw_overrides = os.environ.get("RESEARCH_PROGRAM_PLOT_OVERRIDES")
    if not raw_overrides:
        return {}
    try:
        loaded = json.loads(raw_overrides)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(config_name): overrides
        for config_name, overrides in loaded.items()
        if isinstance(overrides, dict)
    }


def _apply_plot_overrides(config: object, config_name: str):
    overrides = _plot_overrides().get(config_name)
    if not overrides:
        return config
    dataclass_fields = getattr(config, "__dataclass_fields__", {})
    clean_overrides = {
        field_name: value
        for field_name, value in overrides.items()
        if field_name in dataclass_fields
    }
    if not clean_overrides:
        return config
    return replace(config, **clean_overrides)


@dataclass(frozen=True)
class VisualizePhaseDiffConfig:
    results_dir: Path = Path("data/runs")
    graphs_dir: Path = Path("outputs/figures/phase_diff_graphs")

    phase_diff_mode: str = "actual_interval"
    # "cycle_time" または "actual_interval"

    hide_filled_cycles: bool = False

    # x軸範囲。None なら自動
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # フォントサイズ
    font_size_label: int = 30
    font_size_title: int = 16
    font_size_legend: int = 10
    font_size_ticks: int = 35

    # タイトルを付けるか
    show_title: bool = False

    #凡例を付けるか
    show_legend: bool = False

    # y軸範囲のモード
    #y_range_mode: YRangeMode = "0_to_2pi"
    y_range_mode: YRangeMode = "minus_pi_to_pi"
    # "0_to_2pi" または "minus_pi_to_pi"

    # 図サイズ
    figure_width: float = 10.0
    figure_height: float = 10.0

    # 保存dpi
    save_dpi: int = 300

    # 散布図の点サイズ
    scatter_size: float = 12.0

VISUALIZE_PHASE_DIFF_CONFIG = _apply_plot_overrides(
    VisualizePhaseDiffConfig(
        xlim_min = 0,
        xlim_max = 1000,
        hide_filled_cycles = False,
    ),
    "VISUALIZE_PHASE_DIFF_CONFIG",
)



@dataclass(frozen=True)
class PhaseGapErrorPlotConfig:
    results_dir: Path = Path("data/runs")
    graphs_dir: Path = Path("outputs/figures/phase_gap_error_graphs")

    # x軸範囲。None なら自動
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # y軸範囲。None なら自動
    ylim_min: Optional[float] = None
    ylim_max: Optional[float] = None

    # フォントサイズ
    font_size_label: int = 14
    font_size_title: int = 16
    font_size_legend: int = 10
    font_size_ticks: int = 11

    # タイトルを付けるか
    show_title: bool = True

    # 図サイズ
    figure_width: float = 10.0
    figure_height: float = 6.0

    # 保存dpi
    save_dpi: int = 300

    # 散布図の点サイズ
    scatter_size: float = 12.0

@dataclass(frozen=True)
class AggregatedPhaseGapErrorPlotConfig:
    aggregated_stats_dir: Path = Path("data/aggregated")
    graphs_dir: Path = Path("outputs/figures/aggregated_stats_graphs")

    # 描画対象の coupling_function を指定
    # 空リストなら全て対象
    target_coupling_functions: list[str] = field(default_factory=list)

    # coupling_strength の範囲指定
    # None なら制限なし
    coupling_strength_min: Optional[int] = None
    coupling_strength_max: Optional[int] = None

    # x軸範囲
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # y軸範囲
    ylim_min: Optional[float] = None
    ylim_max: Optional[float] = None

    # タイトル表示
    show_title: bool = True

    # フォントサイズ
    font_size_label: int = 14
    font_size_title: int = 16
    font_size_legend: int = 10
    font_size_ticks: int = 11

    # 図サイズ
    figure_width: float = 10.0
    figure_height: float = 6.0

    # 保存dpi
    save_dpi: int = 300

    # 線や点
    show_mean_line: bool = True
    show_min_line: bool = True
    show_max_line: bool = True
    show_q25_q75_band: bool = True

    mean_linewidth: float = 2.0
    minmax_linewidth: float = 1.2

    # 凡例
    show_legend: bool = True

@dataclass(frozen=True)
class CouplingStrengthFilterRule:
    strength_min: Optional[int] = None
    strength_max: Optional[int] = None
    step: Optional[int] = None


@dataclass(frozen=True)
class AggregatedPhaseGapErrorOverlayPlotConfig:
    aggregated_stats_dir: Path = Path("data/aggregated")
    graphs_dir: Path = Path("outputs/figures/aggregated_stats_overlay_graphs")

    # 描画対象の coupling_function を指定。空なら全て対象
    target_coupling_functions: list[str] = field(default_factory=list)

    # coupling_strength の範囲指定。None なら制限なし
    coupling_strength_min: Optional[int] = None
    coupling_strength_max: Optional[int] = None

    # x軸範囲
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # y軸範囲
    ylim_min: Optional[float] = None
    ylim_max: Optional[float] = None

    # タイトル表示
    show_title: bool = False

    # フォントサイズ
    font_size_label: int = 20
    font_size_title: int = 16
    font_size_legend: int = 10
    font_size_ticks: int = 20

    # 図サイズ
    figure_width: float = 10.0
    figure_height: float = 8.0

    # 保存dpi
    save_dpi: int = 300

    # 線幅
    linewidth: float = 2.0

    # 凡例
    show_legend: bool = True

    # 出力ファイル名
    output_filename: str = "overlay_mean_phase_gap_error_ratio.pdf"

    # coupling_function ごとの基準色
    coupling_function_base_colors: dict[str, str] = field(
        default_factory=lambda: {
            "KURAMOTO": "tab:blue",
            "LINEAR": "tab:orange",
            "SIGMOID": "tab:green",
            "PULSE": "tab:red",
        }
    )

    # coupling_function ごとの線種
    coupling_function_line_styles: dict[str, str] = field(
        default_factory=lambda: {
            "KURAMOTO": "-",
            "LINEAR": "--",
            "SIGMOID": "-.",
            "PULSE": ":",
        }
    )

    # coupling_strength の小さい側をどれだけ薄くするか。
    # 0に近いほど濃く，1に近いほど白に近づく。
    min_strength_white_mix: float = 0.70
    max_strength_white_mix: float = 0.10

    # coupling_function ごとの個別フィルタ
    # 例:
    # {
    #   "KURAMOTO": CouplingStrengthFilterRule(strength_min=100, strength_max=500, step=50),
    #   "LINEAR": CouplingStrengthFilterRule(strength_min=200, strength_max=400, step=100),
    # }
    coupling_function_strength_rules: dict[str, CouplingStrengthFilterRule] = field(default_factory=dict)

    # 個別ルールが無い coupling_function に対して使う全体用 step
    coupling_strength_step: Optional[int] = None

    # 収束位置表示
    show_convergence_marker: bool = True
    convergence_window_cycles: int = 50
    convergence_threshold: float = 0.01
    convergence_marker_size: float = 40.0
    convergence_marker_style: str = "o"

        # 各線に coupling_strength を表示するか
    show_strength_label: bool = True

    # 表示位置。 "end" なら線の終端付近に表示
    strength_label_position: str = "end"

    # フォントサイズ
    strength_label_font_size: int = 9
    



@dataclass(frozen=True)
class ConvergenceAnalysisConfig:
    aggregated_stats_dir: Path = Path("data/aggregated")
    graphs_dir: Path = Path("outputs/figures/convergence_graphs")

    # 対象の coupling_function。空なら全て
    target_coupling_functions: list[str] = field(default_factory=list)

    # coupling_strength 範囲。None なら制限なし
    coupling_strength_min: Optional[int] = None
    coupling_strength_max: Optional[int] = None

    # coupling_function ごとの個別ルール
    coupling_function_strength_rules: dict[str, "CouplingStrengthFilterRule"] = field(default_factory=dict)

    # 個別ルールが無い場合の全体 step
    coupling_strength_step: Optional[int] = None

    # 収束判定
    convergence_window_cycles: int = 50
    convergence_threshold: float = 0.01

    # 収束しなかったものをプロットに含めるか
    include_not_converged: bool = True

    # x軸範囲
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # 左軸範囲
    ylim_left_min: Optional[float] = None
    ylim_left_max: Optional[float] = None

    # 右軸範囲
    ylim_right_min: Optional[float] = None
    ylim_right_max: Optional[float] = None

    # タイトル表示
    show_title: bool = False

    # フォント
    font_size_label: int = 14
    font_size_title: int = 16
    font_size_legend: int = 10
    font_size_ticks: int = 11

    # 図サイズ
    figure_width: float = 10.0
    figure_height: float = 6.0

    save_dpi: int = 300
    output_filename: str = "convergence_summary.pdf"

    # coupling_function ごとの色
    coupling_function_base_colors: dict[str, str] = field(
        default_factory=lambda: {
            "KURAMOTO": "tab:blue",
            "LINEAR": "tab:orange",
            "NewSIN": "tab:green",
            "PULSE": "tab:red",
            "FROGCHORUS": "tab:purple",
        }
    )

    # coupling_function ごとの線種
    coupling_function_line_styles: dict[str, str] = field(
        default_factory=lambda: {
            "KURAMOTO": "-",
            "LINEAR": "--",
            "SIGMOID": "-.",
            "PULSE": ":",
            "FROGCHORUS": "-",
        }
    )










AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG = _apply_plot_overrides(
    AggregatedPhaseGapErrorOverlayPlotConfig(
        xlim_min = 50,
        xlim_max = 200,
        ylim_min = 0,
        ylim_max = 1,

        figure_width = 10.0,
        figure_height = 8.0,

        output_filename = "nolta2026_fig.pdf",
        #target_coupling_functions=["KURAMOTO", "LINEAR","NewSIN"],
        target_coupling_functions=["KURAMOTO","LINEAR"],
        coupling_strength_min=100,
        coupling_strength_max=500,
        coupling_function_strength_rules={
            "KURAMOTO": CouplingStrengthFilterRule(
                strength_min=200,
                strength_max=300,
                step=50,
            ),
            "LINEAR": CouplingStrengthFilterRule(
                strength_min=30,
                strength_max=50,
                step=10,
            ),
            "NewSIN":CouplingStrengthFilterRule(
                strength_min=70,
                strength_max=90,
                step=10,
            )#70,80,90が同じくらい
        },
        show_convergence_marker = False,
        convergence_window_cycles = 10,
        convergence_threshold = 0.01,

        show_strength_label=True,
        strength_label_position="middle",
    


        # フォントサイズ
        font_size_label = 30,
        font_size_title = 16,
        font_size_legend = 20,
        font_size_ticks = 30,
        strength_label_font_size=20,
    ),
    "AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG",
)

AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG = _apply_plot_overrides(
    AggregatedPhaseGapErrorPlotConfig(),
    "AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG",
)

PHASE_GAP_ERROR_PLOT_CONFIG = _apply_plot_overrides(
    PhaseGapErrorPlotConfig(),
    "PHASE_GAP_ERROR_PLOT_CONFIG",
)





CONVERGENCE_ANALYSIS_CONFIG = _apply_plot_overrides(
    ConvergenceAnalysisConfig(
        target_coupling_functions=["KURAMOTO", "LINEAR","NewSIN"],
        coupling_function_strength_rules={
            "KURAMOTO": CouplingStrengthFilterRule(strength_min=200, strength_max=300, step=50),
            "LINEAR": CouplingStrengthFilterRule(strength_min=30, strength_max=50, step=10),
            "NewSIN": CouplingStrengthFilterRule(strength_min=70, strength_max=90, step=10),
        },
        convergence_window_cycles=10,
        convergence_threshold=0.01,
    ),
    "CONVERGENCE_ANALYSIS_CONFIG",
)

@dataclass(frozen=True)
class PerPlotConfig:
    results_dir: Path = Path("data/runs")
    graphs_dir: Path = Path("outputs/figures/per_graphs")

    # PER計算窓幅
    per_window_width_cycles: int = 50

    # 変化量計算幅
    # 例: 50なら，PER[c] - PER[c-50] を計算
    per_change_width_cycles: int = 50

    # 変化量グラフを作るか
    show_per_change_plot: bool = True

    # x軸範囲
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # PERグラフのy軸範囲
    per_ylim_min: Optional[float] = None
    per_ylim_max: Optional[float] = None

    # 変化量グラフのy軸範囲
    per_change_ylim_min: Optional[float] = None
    per_change_ylim_max: Optional[float] = None

    # フォントサイズ
    font_size_label: int = 14
    font_size_title: int = 16
    font_size_ticks: int = 11

    # タイトル表示
    show_title: bool = True

    # 図サイズ
    figure_width: float = 10.0
    figure_height: float = 6.0

    # 保存dpi
    save_dpi: int = 300

    # 点サイズ
    scatter_size: float = 10.0


PER_PLOT_CONFIG = _apply_plot_overrides(
    PerPlotConfig(
        xlim_min = 0,
        xlim_max=500,
        per_ylim_max= 100,
        per_ylim_min= 0,
        per_window_width_cycles = 10,
        per_change_width_cycles = 1,
    ),
    "PER_PLOT_CONFIG",
)


@dataclass(frozen=True)
class ComparePerByDevicesIntervalConfig:
    results_dir: Path = Path("data/runs")
    graphs_dir: Path = Path("outputs/figures/compare_per_graphs")

    # 線種とマーカーの候補
    line_styles: tuple[str, ...] = ("-", "--", "-.", ":")
    marker_styles: tuple[str, ...] = ("o", "s", "^", "D", "v", "P", "X", "*")

    # 線幅
    line_width: float = 1.8

    # マーカーサイズ
    marker_size: float = 7.5

    # PER算出窓幅
    per_window_width_cycles: int = 10

    # 比較したいサイクル番号
    target_cycle: int = 90

    # x軸範囲
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # y軸範囲
    ylim_min: Optional[float] = 0
    ylim_max: Optional[float] = 10

    # フォント
    font_size_label: int = 30
    font_size_title: int = 16
    font_size_legend: int = 25
    font_size_ticks: int = 25

    show_title: bool = False

    figure_width: float = 10.0
    figure_height: float = 6.0
    save_dpi: int = 300

    # 点や線
    marker_size: float = 8.0
    line_width: float = 1.5
    # 既存の集計CSVがあれば再計算せずに使うか
    use_existing_csv_if_available: bool = True

    # 比較グラフに載せる手法。空なら全手法
    target_coupling_functions: tuple[str, ...] = ()

    # 手法比較グラフを作るか
    show_combined_method_plot: bool = True

    # 画像全体に Sample 透かしを入れるか
    show_sample_watermark: bool = False

    # 透かし文字
    sample_watermark_text: str = "Sample Sample Sample"

    # 透かしの見た目
    sample_watermark_font_size: int = 48
    sample_watermark_alpha: float = 0.18
    sample_watermark_rotation: float = 30.0
    coupling_function_base_colors: dict[str, str] = field(
        default_factory=lambda: {
            "KURAMOTO": "tab:blue",
            "LINEAR": "tab:orange",
            "FROGCHORUS": "tab:orange",
            "NONE": "tab:gray",
        }
    )

    # send_interval の小さい側，大きい側をどのくらい白に近づけるか
    # 0 に近いほど濃い
    min_interval_white_mix: float = 0.0
    max_interval_white_mix: float = 0.6


@dataclass(frozen=True)
class PerByCouplingStrengthPlotConfig:
    results_dir: Path = Path("data/runs")
    graphs_dir: Path = Path("outputs/figures/per_by_coupling_strength_graphs")

    target_time_ms: float = 300000.0
    per_window_width_cycles: int = 10

    target_coupling_functions: tuple[str, ...] = ()
    coupling_strength_min: Optional[float] = None
    coupling_strength_max: Optional[float] = None

    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None
    ylim_min: Optional[float] = 0
    ylim_max: Optional[float] = 100

    font_size_label: int = 30
    font_size_title: int = 16
    font_size_ticks: int = 25

    show_title: bool = False

    figure_width: float = 10.0
    figure_height: float = 6.0
    save_dpi: int = 300

    marker_style: str = "o"
    marker_size: float = 8.0
    line_style: str = "-"
    line_width: float = 1.5

    show_error_bars: bool = True
    error_bar_capsize: float = 4.0

    use_existing_csv_if_available: bool = False


PER_BY_COUPLING_STRENGTH_PLOT_CONFIG = _apply_plot_overrides(
    PerByCouplingStrengthPlotConfig(),
    "PER_BY_COUPLING_STRENGTH_PLOT_CONFIG",
)


COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG = _apply_plot_overrides(
    ComparePerByDevicesIntervalConfig(
        target_coupling_functions=(),
        use_existing_csv_if_available=False,

    
        target_cycle = 90,
        per_window_width_cycles = 10,
    ),
    "COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG",
)






@dataclass(frozen=True)
class PerAlignedPlotConfig:
    results_dir: Path = Path("data/runs")
    graphs_dir: Path = Path("outputs/figures/per_aligned_graphs")

    # PER計算窓幅
    per_window_width_cycles: int = 5

    # 基準にするサイクル番号
    # このサイクルを 0 として揃える
    base_cycle: int = 0

    # 個別runの図も保存するか
    save_individual_plots: bool = False

    # 重ね描き図を保存するか
    save_overlay_plot: bool = True

    # x軸範囲
    xlim_min: Optional[float] = None
    xlim_max: Optional[float] = None

    # y軸範囲
    ylim_min: Optional[float] = None
    ylim_max: Optional[float] = None

    # フォント
    font_size_label: int = 20
    font_size_title: int = 16
    font_size_legend: int = 15
    font_size_ticks: int = 20

    show_title: bool = True

    figure_width: float = 10.0
    figure_height: float = 6.0
    save_dpi: int = 300

    line_width: float = 1.5
     # run_id ごとに基準サイクルを直接指定する
    # 例:
    # {
    #   "run_0001": 120,
    #   "kura_30mac_250K_15sec": 80,
    # }
    per_aligned_base_cycle_by_run_id: dict[str, int] = field(default_factory=dict)
    # 基準サイクルの決め方
    # "fixed" / "best_per" / "largest_improvement" / "first_available"
    base_cycle_mode: str = "fixed"

    # base_cycle_mode == "fixed" のときだけ使う
    fixed_base_cycle: int = 100

    # base_cycle_mode == "largest_improvement" のとき使う
    per_change_width_cycles: int = 50

    legend_name_by_run_id: dict[str, str] = field(default_factory=dict)
    legend_loc: str = "upper left"
    legend_bbox_to_anchor: tuple[float, float] | None = (1.02, 1.0)



PER_ALIGNED_PLOT_CONFIG = _apply_plot_overrides(
    PerAlignedPlotConfig(
        results_dir=Path("data/runs"),
        graphs_dir=Path("outputs/figures/per_aligned_graphs"),

        #per_window_width_cycles=10,

        # 指定が無いrunだけ fallback で使う
        base_cycle_mode="first_available",
        fixed_base_cycle=100,

    
        # run_idごとに個別指定
        #per_aligned_base_cycle_by_run_id={
        #    "kura_20_10_30mac_300K_15sec_01": 211,
        #    "LIN_20_10_30mac_50K_15sec": 306,
        #},

        legend_name_by_run_id={
            "kura_50mac_50K_15sec_same": "Kuramoto(K=0.005)",
            "kura_50mac_350K_15sec_same": "Kuramoto(K=0.035)",
            "lin_50mac_50K_15sec_same": "FrogChorus(K=0.005)",
        },

        save_individual_plots=True,
        save_overlay_plot=True,

        xlim_min=0,
        xlim_max=500,
        ylim_min=0,
        ylim_max=100,

        show_title=False,
        figure_width=12.0,
        figure_height=6.0,
        save_dpi=300,
        line_width=1.5,

        # フォント
        font_size_label = 30,
        font_size_title = 16,
        font_size_legend = 15,
        font_size_ticks = 30,
    ),
    "PER_ALIGNED_PLOT_CONFIG",
)
