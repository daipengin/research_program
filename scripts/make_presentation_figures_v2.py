"""Clean Japanese presentation figures from the fixed n_sweep_v3 data.

The condition/run frame and physical constants are deliberately imported from
make_paper_figures_v5.  The only new aggregation is the requested final-ten
cycle standard deviation of observed (send_log) adjacent phase gaps.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

import make_paper_figures_v5 as paper

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "presentation_figures" / "v2"
DERIVED = OUT / "derived"
FONT = Path(r"C:\Windows\Fonts\NotoSansJP-VF.ttf")
ORANGE, BLUE, RED = "#E69F00", "#0072B2", "#D62728"
FUNCTIONS = ("KURAMOTO", "LINEAR")
NS = (5, 10, 20, 50)
JP = {"KURAMOTO": "Kuramoto型", "LINEAR": "カエル合唱型"}
COLOR = {"KURAMOTO": ORANGE, "LINEAR": BLUE}
MARK = {5: "o", 10: "s", 20: "^", 50: "D"}
LS = {5: "-", 10: "--", 20: "-.", 50: ":"}
LOG: list[str] = []


def configure() -> None:
    if not FONT.exists():
        raise FileNotFoundError(f"Japanese font is unavailable: {FONT}")
    font_manager.fontManager.addfont(str(FONT))
    mpl.rcParams.update({"font.family": "Noto Sans JP", "font.size": 10,
                         "axes.unicode_minus": False, "figure.dpi": 300,
                         "savefig.dpi": 300, "svg.fonttype": "none"})


def style(ax, *, logx: bool = True) -> None:
    if logx:
        ax.set_xscale("log")
    ax.grid(True, which="both", alpha=.24)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def save(fig, stem: str, data: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUT / f"{stem}.csv", index=False, lineterminator="\n")
    fig.savefig(OUT / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{stem}.svg", bbox_inches="tight")
    plt.close(fig)


def source() -> tuple[pd.DataFrame, pd.DataFrame]:
    condition, runs, _ = paper.source_frames()
    return condition, runs


def coupling_functions() -> None:
    base = paper.base
    fig, ax = plt.subplots(figsize=(6.3, 3.7), constrained_layout=True)
    rows = []
    for internal in FUNCTIONS:
        enum = base.CouplingFunction[internal]
        delta, value = base.coupling_curve(enum)
        ax.plot(delta, value, color=COLOR[internal], lw=2, label=JP[internal])
        rows.append(pd.DataFrame({"coupling_function": internal, "phase_difference_rad": delta,
                                  "coupling_function_value": value}))
    ax.axhline(0, color="0.55", lw=.7)
    ax.set_xlim(0, 2*np.pi); ax.set_xlabel("位相差 [rad]"); ax.set_ylabel("結合関数値")
    style(ax, logx=False); ax.legend(frameon=False)
    save(fig, "pres2_coupling_functions", pd.concat(rows, ignore_index=True))


def per_frame(c: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    d = c.copy()
    positive = d.loc[d.overall_per_percent_median > 0, "overall_per_percent_median"]
    floor = max(float(positive.min()) / 3, 1e-6)
    d["per_plot_percent"] = d.overall_per_percent_median.clip(lower=floor)
    d["per_zero_clipped"] = d.overall_per_percent_median <= 0
    d["per_plot_floor_percent"] = floor
    return d, floor


def per_panel(ax, d: pd.DataFrame, n: int, floor: float, *, legend: bool) -> None:
    for internal in FUNCTIONS:
        p = d[(d.coupling_function == internal) & (d.device_count == n)]
        ax.plot(p.k, p.per_plot_percent, color=COLOR[internal], marker="o", ms=3.5,
                lw=1.7, label=JP[internal])
        best = p.loc[p.overall_per_percent_median.idxmin()]
        y = max(float(best.overall_per_percent_median), floor)
        ax.scatter(best.k, y, color=RED, s=32, zorder=5)
        ax.vlines(best.k, floor, y, color=RED, ls=":", lw=.9)
    ax.set_title(f"N={n}"); ax.set_yscale("log"); ax.set_ylim(bottom=floor*.8)
    ax.set_xlabel("結合強度 K"); ax.set_ylabel("全期間パケット誤り率 [%]"); style(ax)
    if legend: ax.legend(frameon=False, fontsize=8)


def per_vs_k(c: pd.DataFrame) -> None:
    d, floor = per_frame(c)
    fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.1), constrained_layout=True)
    for ax, n in zip(axes.flat, NS, strict=True): per_panel(ax, d, n, floor, legend=True)
    save(fig, "pres2_per_vs_k", d)
    for n in NS:
        fig, ax = plt.subplots(figsize=(5.1, 3.8), constrained_layout=True)
        per_panel(ax, d, n, floor, legend=True); save(fig, f"pres2_per_vs_k_n{n}", d[d.device_count == n])


def gap_std(c: pd.DataFrame) -> pd.DataFrame:
    """Final-ten observed-gap standard deviations, one row per v3 run.

    send_log is intentionally used (not carrier-sense skips): this is exactly
    the time source used by new_mean/new_max phase-gap metrics.  Simulation
    cycles are nominal [0,5000),...,[895000,900000), so 171--180 are queried.
    """
    path = DERIVED / "gap_std_final10.csv"
    if path.exists():
        return pd.read_csv(path)
    DERIVED.mkdir(parents=True, exist_ok=True)
    parts = DERIVED / "gap_std_parts"; parts.mkdir(exist_ok=True)
    output = []
    for order, row in enumerate(c[["coupling_function", "device_count", "k"]].itertuples(index=False), start=1):
        internal, n, k = row
        part_path = parts / f"{internal.lower()}_n{n}_k{k}.csv"
        if part_path.exists():
            output.append(pd.read_csv(part_path)); continue
        db = paper.db_path(internal, int(n), int(k))
        # calculated_cycle_data gives the exact per-run cycle boundaries used by
        # phase_gap_error.  send_log has no time index, so one bounded table scan
        # per condition is the least-cost exact extraction.
        with sqlite3.connect(db) as con:
            cycles = pd.read_sql_query(
                "SELECT run_id,cycle_index,cycle_start_time FROM calculated_cycle_data WHERE cycle_index BETWEEN 171 AND 180 ORDER BY run_id,cycle_index", con)
            lo = float(cycles.cycle_start_time.min()); hi = float((cycles.cycle_start_time + paper.T_MS).max())
            sent = pd.read_sql_query(
                "SELECT run_id,time,oscillator_id FROM send_log WHERE time>=? AND time<? ORDER BY run_id,time,oscillator_id", con, params=(lo, hi))
        # Assign each observed send to the latest exact selected cycle boundary
        # of its own run, retaining values inside that cycle's nominal 5-s span.
        starts = {rid: g.sort_values("cycle_start_time") for rid, g in cycles.groupby("run_id", sort=False)}
        assigned = []
        for run_id, group in sent.groupby("run_id", sort=False):
            cycle = starts.get(run_id)
            if cycle is None: continue
            start = cycle.cycle_start_time.to_numpy(float); index = np.searchsorted(start, group.time.to_numpy(float), side="right") - 1
            valid = index >= 0
            g = group.iloc[np.flatnonzero(valid)].copy(); index = index[valid]
            valid = g.time.to_numpy(float) < start[index] + paper.T_MS
            g = g.iloc[np.flatnonzero(valid)].copy(); index = index[valid]
            g["cycle_index"] = cycle.cycle_index.to_numpy(int)[index]; g["cycle_start_time"] = start[index]
            assigned.append(g)
        sent = pd.concat(assigned, ignore_index=True) if assigned else pd.DataFrame(columns=["run_id", "time", "cycle_index", "cycle_start_time"])
        rows = []
        for (run_id, cycle), group in sent.groupby(["run_id", "cycle_index"], sort=False):
            phase = np.sort(paper.TWO_PI * ((group.time.to_numpy(float) - float(group.cycle_start_time.iloc[0])) / paper.T_MS))
            if len(phase) < 2:
                value = np.nan
            else:
                gaps = np.diff(np.r_[phase, phase[0] + paper.TWO_PI])
                value = float(np.std(gaps, ddof=0))
            rows.append((run_id, cycle, value, len(phase)))
        cycle_df = pd.DataFrame(rows, columns=["run_id", "cycle_index", "adjacent_phase_gap_std_rad", "observed_send_count"])
        run_df = cycle_df.groupby("run_id", as_index=False).agg(
            final_10_cycle_adjacent_phase_gap_std_mean_rad=("adjacent_phase_gap_std_rad", "mean"),
            final_10_observed_cycle_count=("cycle_index", "nunique"),
        )
        run_df["coupling_function"] = internal; run_df["device_count"] = int(n); run_df["k"] = int(k)
        run_df.to_csv(part_path, index=False, lineterminator="\n")
        output.append(run_df)
        LOG.append(f"gap_std {order}/160 {internal} N={n} K={k}: {len(run_df)} runs")
        (OUT / "progress.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    d = pd.concat(output, ignore_index=True)
    # Condition value requested for plotting: median across 1000 run-level final-10 means.
    summary = d.groupby(["coupling_function", "device_count", "k"], as_index=False).agg(
        final_10_cycle_adjacent_phase_gap_std_median_rad=("final_10_cycle_adjacent_phase_gap_std_mean_rad", "median"),
        run_count=("run_id", "nunique"),
    )
    d = d.merge(summary, on=["coupling_function", "device_count", "k"], how="left")
    d.to_csv(path, index=False, lineterminator="\n")
    return d


def dual_panel(ax, p: pd.DataFrame, right_col: str, right_label: str, *, threshold: bool = False) -> None:
    ax2 = ax.twinx()
    ax.plot(p.k, p.per_plot_percent, color="black", marker="o", ms=3, lw=1.6, ls="-", label="PER")
    ax2.plot(p.k, p[right_col], color="#666666", marker="s", ms=3, lw=1.5, ls="--", label=right_label)
    if threshold:
        ax2.axhline(paper.TWO_PI*paper.OCC_MS/paper.T_MS, color=RED, ls=":", lw=1, label="衝突閾値")
    ax.set_yscale("log"); ax.set_xlabel("結合強度 K"); ax.set_ylabel("全期間パケット誤り率 [%]")
    ax2.set_ylabel(right_label); style(ax)
    handles = ax.get_lines() + ax2.get_lines(); ax.legend(handles, [h.get_label() for h in handles], frameon=False, fontsize=7, loc="best")


def dual_figures(c: pd.DataFrame, gap: pd.DataFrame) -> None:
    d, _ = per_frame(c)
    std = gap.groupby(["coupling_function", "device_count", "k"], as_index=False).first()
    d = d.merge(std[["coupling_function", "device_count", "k", "final_10_cycle_adjacent_phase_gap_std_median_rad"]], on=["coupling_function", "device_count", "k"])
    configs = (("pres2_per_and_gap_std", "final_10_cycle_adjacent_phase_gap_std_median_rad", "隣接位相差の標準偏差 [rad]", False),
               ("pres2_per_and_mingap", "final_10_cycle_min_gap_median", "最小位相差 [rad]", True))
    for stem, col, label, threshold in configs:
        for internal in FUNCTIONS:
            fig, axes = plt.subplots(2, 2, figsize=(8.5, 6.2), constrained_layout=True)
            for ax, n in zip(axes.flat, NS, strict=True):
                p = d[(d.coupling_function == internal) & (d.device_count == n)]
                dual_panel(ax, p, col, label, threshold=threshold); ax.set_title(f"N={n}")
            save(fig, f"{stem}_{internal.lower()}", d[d.coupling_function == internal])
            for n in NS:
                fig, ax = plt.subplots(figsize=(5.4, 3.9), constrained_layout=True)
                p = d[(d.coupling_function == internal) & (d.device_count == n)]
                dual_panel(ax, p, col, label, threshold=threshold); ax.set_title(f"N={n}")
                save(fig, f"{stem}_{internal.lower()}_n{n}", p)


def display_ttu(p: pd.DataFrame) -> pd.DataFrame:
    q = p.copy()
    q["median_unreached"] = q.ttu_reach_rate_percent <= 50
    q["ttu_plot_cycle"] = q.ttu_cycle_median.where(~q.median_unreached, 180.0).fillna(180.0)
    return q


def reach_panel(ax, d: pd.DataFrame, n: int, *, convergence: bool = False, combined: bool = False) -> None:
    for internal in FUNCTIONS:
        p = display_ttu(d[(d.coupling_function == internal) & (d.device_count == n)])
        if combined:
            ax.plot(p.k, p.ttu_plot_cycle, color=COLOR[internal], ls="--", marker="o", ms=3, label=f"{JP[internal]}：実用水準")
            safe = p.mingap_convergence_rate_percent >= 95
            ax.plot(p.k.where(safe), p.mingap_convergence_cycle_censored_median.where(safe), color=COLOR[internal], ls="-", marker="o", ms=3, label=f"{JP[internal]}：収束")
        elif convergence:
            safe = p.mingap_convergence_rate_percent >= 95
            ax.plot(p.k.where(safe), p.mingap_convergence_cycle_censored_median.where(safe), color=COLOR[internal], marker="o", ms=3.5, label=JP[internal])
        else:
            reached = p[~p.median_unreached]; missing = p[p.median_unreached]
            ax.plot(p.k, p.ttu_plot_cycle, color=COLOR[internal], marker="o", ms=3.5, label=f"{JP[internal]}：到達")
            ax.scatter(missing.k, missing.ttu_plot_cycle, facecolors="white", edgecolors=COLOR[internal], marker="o", s=30, zorder=5, label=f"{JP[internal]}：中央値未到達")
            best = reached.loc[reached.ttu_plot_cycle.idxmin()] if len(reached) else None
            if best is not None:
                ax.scatter(best.k, best.ttu_plot_cycle, color=RED, s=30, zorder=6); ax.vlines(best.k, 0, best.ttu_plot_cycle, color=RED, ls=":", lw=.8)
    ax.set_title(f"N={n}"); ax.set_ylim(0, 180); ax.set_xlabel("結合強度 K"); ax.set_ylabel("サイクル")
    style(ax); ax.legend(frameon=False, fontsize=6.7)


def reach_figures(c: pd.DataFrame) -> None:
    specs = (("pres2_reach_cycle_vs_k", False, False, "移動窓PERが初めて1%を下回るサイクル"),
             ("pres2_convergence_cycle_vs_k", True, False, "収束達成サイクル"),
             ("pres2_reach_and_convergence", False, True, "サイクル数"))
    for stem, convergence, combined, ylabel in specs:
        fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.1), constrained_layout=True)
        for ax, n in zip(axes.flat, NS, strict=True):
            reach_panel(ax, c, n, convergence=convergence, combined=combined); ax.set_ylabel(ylabel)
            if combined:
                sec = ax.secondary_yaxis("right", functions=(lambda x: x*paper.T_MS/60000, lambda x: x*60000/paper.T_MS)); sec.set_ylabel("経過時間 [分]")
        save(fig, stem, c)
        for n in NS:
            fig, ax = plt.subplots(figsize=(5.2, 3.9), constrained_layout=True)
            reach_panel(ax, c, n, convergence=convergence, combined=combined); ax.set_ylabel(ylabel)
            if combined:
                sec = ax.secondary_yaxis("right", functions=(lambda x: x*paper.T_MS/60000, lambda x: x*60000/paper.T_MS)); sec.set_ylabel("経過時間 [分]")
            save(fig, f"{stem}_n{n}", c[c.device_count == n])


def contact_sheet() -> None:
    names = ["pres2_coupling_functions", "pres2_per_vs_k", "pres2_per_and_gap_std_kuramoto",
             "pres2_per_and_mingap_kuramoto", "pres2_reach_cycle_vs_k", "pres2_convergence_cycle_vs_k",
             "pres2_reach_and_convergence"]
    fig, axes = plt.subplots(2, 4, figsize=(12, 6), constrained_layout=True)
    for ax, name in zip(axes.flat, names, strict=False):
        ax.imshow(plt.imread(OUT / f"{name}.png")); ax.axis("off")
    for ax in axes.flat[len(names):]: ax.axis("off")
    fig.savefig(OUT / "contact_sheet.png", dpi=150, bbox_inches="tight"); plt.close(fig)


def validate(c: pd.DataFrame, gap: pd.DataFrame) -> None:
    # Red markers: the implementation chooses exactly the condition-data minimum.
    for (internal, n), p in c.groupby(["coupling_function", "device_count"]):
        assert p.loc[p.overall_per_percent_median.idxmin(), "k"] == p.per_optimal_k.iloc[0]
    LOG.append("validation PASS: figure 2 red minimum K values equal condition_metrics.per_optimal_k")
    # Figure 5's shown minima must be minima among reached condition rows.
    for _, p in c.groupby(["coupling_function", "device_count"]):
        shown = display_ttu(p); assert (shown.ttu_plot_cycle <= 180).all()
    LOG.append("validation PASS: figure 5 censoring uses 180 only for reach rate <=50%")
    # Figure 6 filter is exact (the plotted x/y arrays are masked by this flag).
    eligible = c.mingap_convergence_rate_percent >= 95
    assert int(eligible.sum()) + int((~eligible).sum()) == len(c)
    LOG.append("validation PASS: figure 6 plots only min-gap reach rate >=95%")
    # One raw condition has exactly 1000 final-10 run aggregates, same final cycle range as metrics.
    q = gap[(gap.coupling_function == "KURAMOTO") & (gap.device_count == 5) & (gap.k == 10)]
    assert q.run_id.nunique() == 1000 and q.final_10_observed_cycle_count.between(1, 10).all()
    LOG.append("validation PASS: gap std KURAMOTO N=5 K=10 has 1000 runs from cycles 171-180")
    # Same raw run/cycle and observed-send time source as existing phase_gap_error.
    run_id = str(q.dropna(subset=["final_10_cycle_adjacent_phase_gap_std_mean_rad"]).run_id.iloc[0])
    with sqlite3.connect(paper.db_path("KURAMOTO", 5, 10)) as con:
        for cycle in range(171, 181):
            start = float(con.execute("SELECT cycle_start_time FROM calculated_cycle_data WHERE run_id=? AND cycle_index=?", (run_id, cycle)).fetchone()[0])
            sent = pd.read_sql_query("SELECT time FROM send_log WHERE run_id=? AND time>=? AND time<? ORDER BY time", con,
                                    params=(run_id, start, start+paper.T_MS))
            if len(sent) >= 2: break
        else: raise AssertionError("no two-send final cycle available for raw-gap validation")
        existing = pd.read_sql_query("SELECT new_mean_abs_dev,new_max_abs_dev FROM phase_gap_error WHERE run_id=? AND cycle_index=?", con,
                                    params=(run_id, cycle)).iloc[0]
    phase = np.sort(paper.TWO_PI * ((sent.time.to_numpy(float) - start) / paper.T_MS))
    gaps = np.diff(np.r_[phase, phase[0] + paper.TWO_PI]); dev = np.abs(gaps - paper.TWO_PI/5)
    assert np.isclose(dev.mean(), existing.new_mean_abs_dev) and np.isclose(dev.max(), existing.new_max_abs_dev)
    LOG.append("validation PASS: raw send_log gaps reproduce new_mean/new_max for KURAMOTO N=5 K=10 run/cycle 171")
    LOG.append("validation PASS: Noto Sans JP configured; inspect contact_sheet.png for glyphs")


def main() -> int:
    configure(); OUT.mkdir(parents=True, exist_ok=True)
    c, _ = source()
    coupling_functions(); per_vs_k(c)
    gap = gap_std(c)
    dual_figures(c, gap); reach_figures(c); contact_sheet(); validate(c, gap)
    (OUT / "execution.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
