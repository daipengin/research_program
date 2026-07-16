"""Add annotated and time-axis variants without touching v2 source figures."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import make_presentation_figures_v2 as base

OUT = base.OUT
LOG: list[str] = []


def save_variant(fig, stem: str) -> None:
    fig.savefig(OUT / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{stem}.svg", bbox_inches="tight")
    plt.close(fig)


def label(ax, x: float, y: float, text: str, direction: int) -> None:
    ax.annotate(text, (x, y), xytext=(5, 10 * direction), textcoords="offset points",
                fontsize=7.2, ha="left", va="bottom" if direction > 0 else "top",
                bbox={"facecolor": "white", "alpha": .84, "edgecolor": "none", "pad": 1.0},
                arrowprops={"arrowstyle": "-", "lw": .45, "color": "0.3"})


def annotated_per(c: pd.DataFrame) -> None:
    d, floor = base.per_frame(c)
    fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.1), constrained_layout=True)
    for ax, n in zip(axes.flat, base.NS, strict=True):
        for direction, internal in zip((1, -1), base.FUNCTIONS, strict=True):
            p = d[(d.coupling_function == internal) & (d.device_count == n)]
            ax.plot(p.k, p.per_plot_percent, color=base.COLOR[internal], marker="o", ms=3.5, lw=1.7, label=base.JP[internal])
            best = p.loc[p.overall_per_percent_median.idxmin()]
            y = max(float(best.overall_per_percent_median), floor)
            ax.scatter(best.k, y, color=base.RED, s=32, zorder=5); ax.vlines(best.k, floor, y, color=base.RED, ls=":", lw=.9)
            label(ax, float(best.k), y, f"K={int(best.k)}, {best.overall_per_percent_median:.2g}%", direction)
            LOG.append(f"per label PASS N={n} {internal} K={best.k:g} per={best.overall_per_percent_median:g}")
        ax.set_title(f"N={n}"); ax.set_yscale("log"); ax.set_ylim(bottom=floor*.8); ax.set_xlabel("結合強度 K"); ax.set_ylabel("全期間パケット誤り率 [%]")
        base.style(ax); ax.legend(frameon=False, fontsize=8)
    save_variant(fig, "pres2_per_vs_k_annotated")


def reach_data(c: pd.DataFrame, internal: str, n: int) -> pd.DataFrame:
    return base.display_ttu(c[(c.coupling_function == internal) & (c.device_count == n)])


def annotated_cycles(c: pd.DataFrame, *, convergence: bool) -> None:
    stem = "pres2_convergence_cycle_vs_k_annotated" if convergence else "pres2_reach_cycle_vs_k_annotated"
    ylabel = "収束達成サイクル" if convergence else "移動窓PERが初めて1%を下回るサイクル"
    fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.1), constrained_layout=True)
    for ax, n in zip(axes.flat, base.NS, strict=True):
        for direction, internal in zip((1, -1), base.FUNCTIONS, strict=True):
            p = reach_data(c, internal, n)
            if convergence:
                shown = p[p.mingap_convergence_rate_percent >= 95]
                ax.plot(shown.k, shown.mingap_convergence_cycle_censored_median, color=base.COLOR[internal], marker="o", ms=3.5, label=base.JP[internal])
                if len(shown):
                    best = shown.loc[shown.mingap_convergence_cycle_censored_median.idxmin()]
                    value = float(best.mingap_convergence_cycle_censored_median)
            else:
                ax.plot(p.k, p.ttu_plot_cycle, color=base.COLOR[internal], marker="o", ms=3.5, label=f"{base.JP[internal]}：到達")
                missing = p[p.median_unreached]
                ax.scatter(missing.k, missing.ttu_plot_cycle, facecolors="white", edgecolors=base.COLOR[internal], marker="o", s=30, zorder=5, label=f"{base.JP[internal]}：中央値未到達")
                shown = p[~p.median_unreached]
                if len(shown):
                    best = shown.loc[shown.ttu_plot_cycle.idxmin()]; value = float(best.ttu_cycle_median)
            if len(shown):
                ax.scatter(best.k, value, color=base.RED, s=30, zorder=6); ax.vlines(best.k, 0, value, color=base.RED, ls=":", lw=.8)
                label(ax, float(best.k), value, f"K={int(best.k)}, {value:.0f}cyc", direction)
                field = "mingap_convergence_cycle_censored_median" if convergence else "ttu_cycle_median"
                assert np.isclose(value, float(best[field]))
                LOG.append(f"{'convergence' if convergence else 'reach'} label PASS N={n} {internal} K={best.k:g} cycle={value:g}")
        ax.set_title(f"N={n}"); ax.set_ylim(0, 180); ax.set_xlabel("結合強度 K"); ax.set_ylabel(ylabel); base.style(ax); ax.legend(frameon=False, fontsize=6.7)
    save_variant(fig, stem)


def time_panel(ax, c: pd.DataFrame, n: int, *, annotated: bool) -> None:
    factor = base.paper.T_MS / 60000.0
    for direction, internal in zip((1, -1), base.FUNCTIONS, strict=True):
        p = reach_data(c, internal, n)
        ax.plot(p.k, p.ttu_plot_cycle * factor, color=base.COLOR[internal], ls="--", marker="o", ms=3, label=f"{base.JP[internal]}：実用水準")
        safe = p[p.mingap_convergence_rate_percent >= 95]
        y = safe.mingap_convergence_cycle_censored_median * factor
        ax.plot(safe.k, y, color=base.COLOR[internal], ls="-", marker="o", ms=3, label=f"{base.JP[internal]}：収束")
        if annotated and len(safe):
            best = safe.loc[safe.mingap_convergence_cycle_censored_median.idxmin()]
            minute = float(best.mingap_convergence_cycle_censored_median * factor)
            ax.scatter(best.k, minute, color=base.RED, s=30, zorder=6)
            label(ax, float(best.k), minute, f"K={int(best.k)}, {minute:.1f}分", direction)
            assert np.isclose(minute, float(best.mingap_convergence_cycle_censored_median) * 5 / 60)
            LOG.append(f"time label PASS N={n} {internal} K={best.k:g} min={minute:g}")
    ax.set_title(f"N={n}"); ax.set_ylim(0, 15); ax.set_yticks(np.arange(0, 15.1, 2.5)); ax.set_xlabel("結合強度 K"); ax.set_ylabel("経過時間 [分]")
    base.style(ax); ax.legend(frameon=False, fontsize=6.5)
    sec = ax.secondary_yaxis("right", functions=(lambda x: x / factor, lambda x: x * factor)); sec.set_ylabel("サイクル数")


def time_variants(c: pd.DataFrame) -> None:
    for annotated in (False, True):
        fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.1), constrained_layout=True)
        for ax, n in zip(axes.flat, base.NS, strict=True): time_panel(ax, c, n, annotated=annotated)
        save_variant(fig, "pres2_reach_and_convergence_time_annotated" if annotated else "pres2_reach_and_convergence_time")


def contact_sheet() -> None:
    names = ["pres2_coupling_functions", "pres2_per_vs_k", "pres2_per_vs_k_annotated",
             "pres2_per_and_gap_std_kuramoto", "pres2_per_and_mingap_kuramoto",
             "pres2_reach_cycle_vs_k", "pres2_reach_cycle_vs_k_annotated",
             "pres2_convergence_cycle_vs_k", "pres2_convergence_cycle_vs_k_annotated",
             "pres2_reach_and_convergence", "pres2_reach_and_convergence_time",
             "pres2_reach_and_convergence_time_annotated"]
    fig, axes = plt.subplots(3, 4, figsize=(12, 9), constrained_layout=True)
    for ax, name in zip(axes.flat, names, strict=True): ax.imshow(plt.imread(OUT / f"{name}.png")); ax.axis("off")
    fig.savefig(OUT / "contact_sheet.png", dpi=150, bbox_inches="tight"); plt.close(fig)


def main() -> int:
    base.configure(); c, _ = base.source()
    annotated_per(c); annotated_cycles(c, convergence=False); annotated_cycles(c, convergence=True); time_variants(c); contact_sheet()
    (OUT / "execution_additions.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
