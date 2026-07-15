"""Generate manuscript figures from the fixed n_sweep_v3 data set (no simulation)."""
from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from mpl_toolkits.axes_grid1.inset_locator import inset_axes

import make_paper_figures as base


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "n_sweep_v3"
RAW = Path(r"F:\research_program_data\n_sweep_v3_raw")
OUT = ROOT / "results" / "paper_figures_v5"
T_MS = 5000.0
OCC_MS = 25.544
TWO_PI = 2.0 * math.pi
ORANGE, BLUE, RED = "#E69F00", "#0072B2", "#D62728"
N_STYLE = {5: ("o", "-", 1.00), 10: ("s", "--", 0.82), 20: ("^", "-.", 0.67), 50: ("D", ":", 0.52)}
FUNCTION = {"KURAMOTO": ("Kuramoto based", ORANGE), "LINEAR": ("frog chorus based", BLUE)}
CAPTIONS: list[tuple[str, str]] = []


def fig_style(subfigure: bool):
    return base.apply_style(2.0 if subfigure else 1.15)


def polish(ax, style):
    base.style_axis(ax, style)
    ax.set_xscale("log")


def save(fig, stem: str, data: pd.DataFrame, caption: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUT / f"{stem}.csv", index=False)
    fig.savefig(OUT / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)
    CAPTIONS.append((stem, caption))


def source_frames() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    condition = pd.read_csv(RESULTS / "condition_metrics.csv").sort_values(
        ["coupling_function", "device_count", "k"]
    )
    run = pd.read_csv(RESULTS / "run_metrics.csv")
    meta = json.loads((RESULTS / "metadata.json").read_text(encoding="utf-8"))
    return condition, run, meta


def eps_for(n: int) -> float:
    return TWO_PI / n - TWO_PI * OCC_MS / T_MS


def label_function(f: str) -> str:
    return FUNCTION[f][0]


def fig1_scatter(c: pd.DataFrame) -> None:
    style = fig_style(False)
    d = c.copy()
    positive = d.loc[d.overall_per_percent_median > 0, "overall_per_percent_median"]
    floor = max(float(positive.min()) / 3.0 if len(positive) else 1e-4, 1e-6)
    d["per_plot_percent"] = d.overall_per_percent_median.clip(lower=floor)
    d["per_zero_clipped"] = d.overall_per_percent_median <= 0
    fig, ax = plt.subplots(figsize=(5.3, 3.45), constrained_layout=True)
    for f, group in d.groupby("coupling_function"):
        for n, part in group.groupby("device_count"):
            marker = N_STYLE[int(n)][0]
            ax.scatter(part.final_10_cycle_new_max_abs_dev_median, part.per_plot_percent,
                       c=FUNCTION[f][1], marker=marker, s=23, alpha=0.82,
                       edgecolors="black", linewidths=0.25, label=f"{label_function(f)}, N={n}")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Final 10-cycle median max phase-gap deviation [rad]")
    ax.set_ylabel("Median PER [%]")
    ax.text(0.02, 0.02, f"Zero PER plotted at {floor:.1e}%", transform=ax.transAxes, fontsize=style.annotation_size)
    n5 = d[d.device_count.eq(5)]
    if len(n5):
        x, y = n5.final_10_cycle_new_max_abs_dev_median.max(), n5.per_plot_percent.median()
        ax.annotate("N=5: large fluctuation,\nnear-zero PER", (x, y), xytext=(-120, 38), textcoords="offset points",
                    arrowprops={"arrowstyle": "->", "lw": 0.8}, fontsize=style.annotation_size)
    bad = d[(d.coupling_function.eq("KURAMOTO")) & (d.device_count.eq(50)) & (d.k <= 70)]
    if len(bad):
        ax.scatter(bad.final_10_cycle_new_max_abs_dev_median, bad.per_plot_percent, s=100,
                   facecolors="none", edgecolors=RED, linewidths=1.1)
        ax.annotate("Kuramoto N=50, K≤70:\nsmall fluctuation, high PER",
                    (bad.final_10_cycle_new_max_abs_dev_median.median(), bad.per_plot_percent.median()),
                    xytext=(28, -48), textcoords="offset points", arrowprops={"arrowstyle": "->", "lw": 0.8},
                    fontsize=style.annotation_size)
    ax.legend(frameon=False, fontsize=6.5, ncol=2, loc="upper left")
    base.style_axis(ax, style)
    save(fig, "fig_scatter_fluctuation_vs_per", d,
         "Each point is one function-N-K condition. Small steady-state phase-gap fluctuation does not by itself imply a low packet error rate; the two annotated regions show the two principal counterexamples.")


RATE_COLS = [("ttu_reach_rate_percent", "TTU", "#222222", "-"),
             ("mingap_convergence_rate_percent", "Intended min-gap", "#009E73", "-"),
             ("max_convergence_rate_percent", "Max deviation", "#777777", "--"),
             ("aux_mean_convergence_rate_percent", "Mean deviation (aux.)", "#B0B0B0", ":")]


def rate_panel(ax, part: pd.DataFrame, style, title: str) -> None:
    for col, label, color, ls in RATE_COLS:
        ax.plot(part.k, part[col], marker="o", ms=style.marker_size, lw=style.line_width, ls=ls, color=color, label=label)
    polish(ax, style); ax.set_ylim(-3, 103); ax.set_ylabel("Reach rate [%]"); ax.set_xlabel("K"); ax.set_title(title)


def fig2_rates(c: pd.DataFrame) -> None:
    style = fig_style(True); data = c.copy()
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9), constrained_layout=True)
    for ax, f in zip(axes, ["KURAMOTO", "LINEAR"], strict=True):
        rate_panel(ax, data[(data.coupling_function == f) & (data.device_count == 50)], style, f"{label_function(f)}, N=50")
    handles, labels = axes[0].get_legend_handles_labels(); fig.legend(handles, labels, loc="outside upper center", ncol=4, frameon=False)
    save(fig, "fig_convergence_rate_vs_ttu_by_k", data[data.device_count.eq(50)],
         "For N=50, the intended min-gap convergence rate follows the TTU reach rate more closely than the mean- or maximum-deviation criteria. Rates are computed across 1000 independent runs per condition.")
    fig, axes = plt.subplots(2, 4, figsize=(9.0, 4.7), constrained_layout=True, sharex=True, sharey=True)
    for ax, (f, n) in zip(axes.flat, [(f, n) for f in ["KURAMOTO", "LINEAR"] for n in [5, 10, 20, 50]], strict=True):
        rate_panel(ax, data[(data.coupling_function == f) & (data.device_count == n)], style, f"{label_function(f)}, N={n}")
    handles, labels = axes.flat[0].get_legend_handles_labels(); fig.legend(handles, labels, loc="outside upper center", ncol=4, frameon=False)
    save(fig, "fig_convergence_rate_vs_ttu_by_k_all_n", data,
         "Appendix view of the four reach-rate criteria over all device counts. The intended min-gap criterion is the collision-free design criterion used in the main text.")


def fig3_fluctuation(c: pd.DataFrame) -> None:
    style = fig_style(True); rows = []; fits = []
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), constrained_layout=True, sharey=True)
    for ax, f in zip(axes, ["KURAMOTO", "LINEAR"], strict=True):
        for n in [5, 10, 20, 50]:
            p = c[(c.coupling_function == f) & (c.device_count == n)].copy()
            marker, ls, alpha = N_STYLE[n]; color = FUNCTION[f][1]
            safe = p.mingap_convergence_rate_percent >= 50
            ax.plot(p.loc[safe, "k"], p.loc[safe, "final_10_cycle_new_max_abs_dev_median"], marker=marker, ls=ls,
                    color=color, alpha=alpha, lw=style.line_width, ms=style.marker_size, label=f"N={n}")
            ax.scatter(p.loc[~safe, "k"], p.loc[~safe, "final_10_cycle_new_max_abs_dev_median"], marker=marker,
                       color=color, alpha=0.25, s=18)
            eps = eps_for(n); ax.axhline(eps, color=color, alpha=alpha, lw=0.8, ls=":")
            crossing = p.iloc[[int(np.argmin(np.abs(p.final_10_cycle_new_max_abs_dev_median - eps)))]]
            ax.scatter(crossing.k, crossing.final_10_cycle_new_max_abs_dev_median, color=RED, s=30, zorder=5)
            eligible = p[(p.k >= 10) & (p.mingap_convergence_rate_percent >= 95)].copy()
            if len(eligible) >= 2:
                a, b = np.polyfit(eligible.k, eligible.final_10_cycle_new_max_abs_dev_median, 1)
                pred = a * eligible.k + b; ss_res = float(np.sum((eligible.final_10_cycle_new_max_abs_dev_median - pred) ** 2)); ss_tot = float(np.sum((eligible.final_10_cycle_new_max_abs_dev_median - eligible.final_10_cycle_new_max_abs_dev_median.mean()) ** 2))
                fits.append({"function": label_function(f), "internal_function": f, "N": n, "a": a, "b": b,
                             "used_k_values": ";".join(map(str, eligible.k.astype(int))), "k_min": eligible.k.min(), "k_max": eligible.k.max(),
                             "r_squared": 1 - ss_res / ss_tot if ss_tot else np.nan, "K_cross": (eps - b) / a if a else np.nan})
            rows.append(p.assign(epsilon_tolerance_rad=eps, fit_eligible=(p.k >= 10) & (p.mingap_convergence_rate_percent >= 95), plot_function=label_function(f)))
        polish(ax, style); ax.set_yscale("log"); ax.set_xlabel("K"); ax.set_title(label_function(f))
    axes[0].set_ylabel("Final 10-cycle median max deviation [rad]")
    axes[0].legend(frameon=False, title="Device count", ncol=2, fontsize=7)
    pd.DataFrame(fits).to_csv(OUT / "slope_fit_results.csv", index=False)
    save(fig, "fig_steady_fluctuation_vs_k", pd.concat(rows, ignore_index=True),
         "Linear fits quantify the local K-dependence of steady maximum phase-gap deviation over the retained high-reach-rate region. Dotted horizontal lines are the N-specific tolerance epsilon_tol; pale isolated markers are conditions with intended min-gap convergence below 50% and are excluded from fitting.")


def fig4_design(c: pd.DataFrame) -> None:
    style = fig_style(False); data = c.copy(); fig, ax = plt.subplots(figsize=(5.2, 3.3), constrained_layout=True)
    for f, z in [("KURAMOTO", 2), ("LINEAR", 3)]:
        color = FUNCTION[f][1]; band = data[data.coupling_function == f].groupby("device_count").first().reset_index()
        n = band.device_count.to_numpy(float); low = band.mingap_safe_k_ge95_lower.to_numpy(float); high = band.mingap_safe_k_ge95_upper.to_numpy(float)
        ax.fill_between(n, low, high, color=color, alpha=0.25, label=f"{label_function(f)} ≥95% band", zorder=z)
        ax.plot(n, band.per_optimal_k, marker="o", color=color, lw=style.line_width, label=f"{label_function(f)} PER optimum", zorder=z+3)
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xticks([5,10,20,50]); ax.set_xticklabels(["5","10","20","50"])
    ax.set_xlabel("Number of devices, N"); ax.set_ylabel("K"); base.style_axis(ax, style); ax.legend(frameon=False, fontsize=7, ncol=2)
    save(fig, "fig_design_map_n_vs_k", data,
         "Shaded envelopes show the K ranges with intended min-gap convergence rate at least 95%; points show the PER-optimal K. The overlapping envelopes provide a compact density-dependent design map.")


def fig5_speed(c: pd.DataFrame) -> None:
    style = fig_style(False); d = c[c.device_count.eq(50)].copy(); fig, ax = plt.subplots(figsize=(5.2, 3.35), constrained_layout=True)
    for f in ["KURAMOTO", "LINEAR"]:
        p = d[d.coupling_function == f]; color = FUNCTION[f][1]
        for col, label, ls in [("ttu_cycle_median", "TTU", "--"), ("mingap_convergence_cycle_censored_median", "Intended min-gap", "-")]:
            ax.plot(p.k, p[col], color=color, ls=ls, marker="o", ms=3, label=f"{label_function(f)}: {label}")
        safe = p[p.mingap_convergence_rate_percent >= 95]
        if len(safe):
            best = safe.loc[safe.mingap_convergence_cycle_censored_median.idxmin()]
            ax.scatter(best.k, best.mingap_convergence_cycle_censored_median, s=40, color=RED, zorder=5)
    polish(ax, style); ax.set_ylabel("Censored median convergence cycle"); ax.set_xlabel("K")
    r = ax.secondary_yaxis("right", functions=(lambda x: x*T_MS/60000, lambda x: x*60000/T_MS)); r.set_ylabel("Time [min]")
    ax.legend(frameon=False, fontsize=6.5, ncol=2)
    save(fig, "fig_convergence_speed_two_metrics", d,
         "For N=50, dashed curves give TTU timing and solid curves give intended min-gap convergence timing. Red markers identify the fastest intended-min-gap point inside each function's at-least-95% reach-rate band.")


def fig6_per(c: pd.DataFrame) -> None:
    style = fig_style(True); d = c.copy(); fig, axes = plt.subplots(1,2, figsize=(7.2,3.0), constrained_layout=True, sharey=True)
    positive = d[d.overall_per_percent_median > 0].overall_per_percent_median
    floor = max(float(positive.min())/3, 1e-6)
    d["per_plot_percent"] = d.overall_per_percent_median.clip(lower=floor)
    for ax, f in zip(axes, ["KURAMOTO", "LINEAR"], strict=True):
        for n in [5,10,20,50]:
            p = d[(d.coupling_function == f)&(d.device_count == n)]; marker, ls, alpha=N_STYLE[n]
            ax.plot(p.k,p.per_plot_percent,marker=marker,ls=ls,color=FUNCTION[f][1],alpha=alpha,label=f"N={n}")
            best = p.loc[p.overall_per_percent_median.idxmin()]
            ax.scatter(best.k, max(best.overall_per_percent_median,floor),color=RED,s=32,zorder=5)
            ax.annotate(f"K={int(best.k)}",(best.k,max(best.overall_per_percent_median,floor)),xytext=(5,8),textcoords="offset points",fontsize=7,color=RED)
        polish(ax,style); ax.set_yscale("log"); ax.set_xlabel("K"); ax.set_title(label_function(f))
    axes[0].set_ylabel("Median PER [%]"); axes[0].legend(frameon=False, title="N")
    save(fig,"fig_per_vs_k",d,"Median PER as a function of K. Red markers show the minimum PER for each device count; the two functions exhibit distinct high-K collapse behavior.")


def fig7_random() -> None:
    style=fig_style(True); rng=np.random.default_rng(20260716); rows=[]
    for n in [5,10,20,50]:
        starts=np.array([rng.choice(5000, size=n, replace=False) for _ in range(10000)])
        phases=np.sort(TWO_PI*starts/5000,axis=1); gaps=np.diff(phases,axis=1); gaps=np.c_[gaps,phases[:,0]+TWO_PI-phases[:,-1]]
        ideal=TWO_PI/n; rows.extend(pd.DataFrame({"N":n,"trial":np.arange(10000),"mean_abs_deviation":np.mean(abs(gaps-ideal),axis=1),"max_abs_deviation":np.max(abs(gaps-ideal),axis=1),"min_gap_rad":np.min(gaps,axis=1)}).to_dict("records"))
    d=pd.DataFrame(rows); fig,axes=plt.subplots(1,3,figsize=(9.0,3.0),constrained_layout=True)
    for ax,col,title in zip(axes,["mean_abs_deviation","max_abs_deviation","min_gap_rad"],["Mean absolute deviation","Maximum absolute deviation","Intended minimum gap"],strict=True):
        vals=[d[d.N==n][col].to_numpy() for n in [5,10,20,50]]; ax.violinplot(vals,positions=[5,10,20,50],showmedians=True)
        for n in [5,10,20,50]:
            threshold=eps_for(n) if col!="min_gap_rad" else TWO_PI*OCC_MS/T_MS
            ax.hlines(threshold,n-1.4,n+1.4,color=RED,lw=1)
        ax.set_xlabel("N");ax.set_title(title);base.style_axis(ax,style)
    axes[0].set_ylabel("Deviation / gap [rad]")
    save(fig,"fig_criterion_random_baseline",d,"Uniform random initial phases were sampled without replacement on the 1-ms grid (10,000 trials per N; seed 20260716). Red segments are the corresponding one-cycle thresholds, showing why an averaged deviation can be permissive even in random configurations.")


def db_path(f:str,n:int,k:int)->Path: return RAW / ("kuramoto" if f=="KURAMOTO" else "linear") / f"n_{n}" / f"k_{k}.sqlite"

def load_demo(f:str,n:int,k:int,run_index:int)->tuple[pd.Series,pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    with sqlite3.connect(db_path(f,n,k)) as con:
        run=pd.read_sql_query("SELECT * FROM runs WHERE random_run_index=?",con,params=(run_index,)).iloc[0]
        rid=run.run_id
        phase=pd.read_sql_query("SELECT cycle_index,min_gap_rad,new_max_abs_dev,new_mean_abs_dev FROM phase_gap_error WHERE run_id=? ORDER BY cycle_index",con,params=(rid,))
        sends=pd.read_sql_query("SELECT time,oscillator_id,send_count FROM send_log WHERE run_id=? ORDER BY time,oscillator_id",con,params=(rid,))
        cs=pd.read_sql_query("SELECT time,oscillator_id,action,carrier_sense_start,carrier_sense_end,blocking_oscillator_id,blocking_transmission_start,blocking_transmission_end FROM carrier_sense_log WHERE run_id=? ORDER BY time",con,params=(rid,))
    return run,phase,sends,cs

def choose_run(r:pd.DataFrame,f:str,n:int,k:int,col:str)->int:
    p=r[(r.coupling_function==f)&(r.device_count==n)&(r.k==k)].copy(); target=p[col].median(skipna=True); return int(p.iloc[np.argmin(abs(p[col]-target))].run_index)

def fig8_transient(r:pd.DataFrame)->None:
    style=fig_style(True); specs=[("KURAMOTO",500,"Kuramoto based, N=50, K=500"),("LINEAR",10,"frog chorus based, N=50, K=10")]; frames=[]
    fig,axes=plt.subplots(1,2,figsize=(7.2,3.0),constrained_layout=True,sharey=True)
    for ax,(f,k,title) in zip(axes,specs,strict=True):
        idx=choose_run(r,f,50,k,"mingap_convergence_cycle"); run,phase,_,_=load_demo(f,50,k,idx); conv=int(r[(r.coupling_function==f)&(r.device_count==50)&(r.k==k)&(r.run_index==idx)].mingap_convergence_cycle.iloc[0]); phase["function"]=f;phase["K"]=k;phase["run_index"]=idx;phase["selected_for_median"] = True;frames.append(phase)
        ax.plot(phase.cycle_index,phase.min_gap_rad,color=FUNCTION[f][1],lw=style.line_width); threshold=TWO_PI*OCC_MS/T_MS;ax.axhline(threshold,color=RED,ls="--",lw=1);ax.axvspan(conv,conv+9,color=RED,alpha=.12);ax.scatter([conv],[phase.loc[phase.cycle_index==conv,"min_gap_rad"].iloc[0]],color=RED,s=28,zorder=5)
        ax.annotate("10-cycle window",(conv,threshold),xytext=(8,18),textcoords="offset points",fontsize=7,arrowprops={"arrowstyle":"->","lw":.7});ax.set_title(title);ax.set_xlabel("Cycle");base.style_axis(ax,style)
        sec=ax.secondary_yaxis("right",functions=(lambda rad:rad*T_MS/TWO_PI,lambda ms:ms*TWO_PI/T_MS));sec.set_ylabel("Gap [ms]")
    axes[0].set_ylabel("Intended minimum gap [rad]")
    save(fig,"fig_transient_demo_min_gap",pd.concat(frames,ignore_index=True),"Representative N=50 trajectories were selected by the run whose intended min-gap convergence cycle is closest to the condition median. The shaded interval is the first qualifying 10-cycle window, and the dashed line is the collision-free gap threshold.")


def fig9_n5(r:pd.DataFrame)->None:
    style=fig_style(True); p=r[(r.coupling_function=="KURAMOTO")&(r.device_count==5)&(r.k==10)&(r.overall_per_percent==0)]; idx=int(p.iloc[0].run_index); run,phase,sends,_=load_demo("KURAMOTO",5,10,idx); start=float(sends.time.min()); sends["cycle_index"]=np.floor((sends.time-start)/T_MS).astype(int)+1;sends["phase_rad"]=TWO_PI*((sends.time-start)%T_MS)/T_MS
    cycle=sends.groupby("cycle_index").size().rename("actual").reindex(range(1,181),fill_value=0);cum=100*(5*np.arange(1,181)-np.minimum(cycle.to_numpy().cumsum(),5*np.arange(1,181)))/(5*np.arange(1,181))
    fig,axes=plt.subplots(2,1,figsize=(4.2,4.1),constrained_layout=True,sharex=True)
    for device,part in sends.groupby("oscillator_id"):
        axes[0].scatter(part.cycle_index,part.phase_rad,s=5,label=f"Device {device}")
    axes[0].axhline(TWO_PI/5,color="0.35",ls="--",lw=.8,label="Ideal gap 2π/5"); axes[0].axhspan(0,TWO_PI*OCC_MS/T_MS,color=RED,alpha=.12,label="Occupied-time width")
    axes[0].set_ylabel("Transmission phase [rad]");axes[0].legend(ncol=3,fontsize=6,frameon=False);base.style_axis(axes[0],style)
    axes[1].plot(np.arange(1,181),cum,color=FUNCTION["KURAMOTO"][1]);axes[1].set_ylabel("Cumulative PER [%]");axes[1].set_xlabel("Cycle");base.style_axis(axes[1],style)
    d=sends.copy();d["run_index"]=idx;d["cumulative_per_percent"]=np.nan; d=pd.concat([d,pd.DataFrame({"cycle_index":np.arange(1,181),"cumulative_per_percent":cum,"record_type":"cumulative_per"})],ignore_index=True)
    save(fig,"fig_n5_demo_uneven_but_lossless",d,"A lossless N=5 Kuramoto-based realization (K=10) retains visibly uneven transmission phases. The cumulative PER remains zero, illustrating that ideal equal spacing is not necessary for collision-free operation.")


def cs_report(r:pd.DataFrame)->None:
    cand=r[(r.coupling_function=="KURAMOTO")&(r.device_count==50)&(r.k==70)&(~r.mingap_converged.astype(bool))].head(3); rows=[]
    for item in cand.itertuples():
        _,_,_,cs=load_demo("KURAMOTO",50,70,int(item.run_index)); skips=cs[cs.action.eq("skip_busy")].copy();last=skips[skips.time>=skips.time.max()-50*T_MS]; counts=last.groupby("oscillator_id").size();top=int(counts.idxmax()) if len(counts) else -1; share=float(counts.max()/counts.sum()) if len(counts) else 0; rows.append({"run_index":item.run_index,"skip_events_last_50_cycles":len(last),"active_devices":len(counts),"top_device":top,"top_device_share":share,"classification":"fixed_starvation" if share>=.5 else "rotating_or_distributed"})
    d=pd.DataFrame(rows);d.to_csv(OUT/"cs_starvation_report.csv",index=False)
    columns=list(d.columns)
    table=["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"]*len(columns)) + " |"]
    table.extend("| " + " | ".join(str(row[column]) for column in columns) + " |" for _,row in d.iterrows())
    (OUT/"cs_starvation_report.md").write_text("# CS starvation check\n\n"+"\n".join(table)+"\n\nA fixed starvation pattern is defined here as one device accounting for at least 50% of final-50-cycle skip events.\n",encoding="utf-8")
    fixed=d[d.classification.eq("fixed_starvation")]
    if fixed.empty:
        return
    selected=fixed.iloc[0]; run_index=int(selected.run_index); highlighted=int(selected.top_device)
    run,phase,sends,cs=load_demo("KURAMOTO",50,70,run_index)
    t0=float(sends.time.min()); cs=cs[cs.action.eq("skip_busy")].copy(); cs["cycle_index"]=np.floor((cs.time-t0)/T_MS).astype(int)+1
    sends=sends.copy(); sends["cycle_index"]=np.floor((sends.time-t0)/T_MS).astype(int)+1
    last_start=max(1,int(cs.cycle_index.max())-49); cs=cs[cs.cycle_index>=last_start]; sends=sends[(sends.cycle_index>=last_start)&(sends.cycle_index<=last_start+49)]
    fig,ax=plt.subplots(figsize=(5.4,3.45),constrained_layout=True); style=fig_style(False)
    ax.scatter(sends.cycle_index,sends.oscillator_id,s=4,color="#555555",alpha=.45,label="Sent")
    ax.scatter(cs.cycle_index,cs.oscillator_id,s=10,color="#9E9E9E",marker="x",label="CS skip")
    hit=cs[cs.oscillator_id.eq(highlighted)]; ax.scatter(hit.cycle_index,hit.oscillator_id,s=25,color=RED,marker="x",label=f"Highlighted device {highlighted}")
    ax.set_xlabel("Cycle");ax.set_ylabel("Device ID");ax.set_title("Kuramoto based, N=50, K=70: CS-skip raster");base.style_axis(ax,style);ax.legend(frameon=False,fontsize=7,ncol=3,loc="upper center")
    inset=inset_axes(ax,width="43%",height="38%",loc="lower right",borderpad=1.2)
    rel=(hit.time.to_numpy()-t0)%T_MS
    for index,row in enumerate(hit.itertuples()):
        blocker_start=(float(row.blocking_transmission_start)-t0)%T_MS if pd.notna(row.blocking_transmission_start) else np.nan
        blocker_end=(float(row.blocking_transmission_end)-t0)%T_MS if pd.notna(row.blocking_transmission_end) else np.nan
        if np.isfinite(blocker_start) and np.isfinite(blocker_end) and blocker_end>=blocker_start:
            inset.hlines(index,blocker_start,blocker_end,color="#555555",lw=2)
        inset.plot(rel[index],index,"x",color=RED,ms=4)
    inset.set_xlabel("Time in cycle [ms]",fontsize=6);inset.set_ylabel("Skip event",fontsize=6);inset.tick_params(labelsize=6);inset.set_title("Intended skip vs. blocker occupancy",fontsize=6)
    raw=pd.concat([sends.assign(record_type="send"),cs.assign(record_type="skip_busy")],ignore_index=True);raw["highlighted_device"]=highlighted;raw["selected_run_index"]=run_index
    save(fig,"fig_cs_starvation_demo",raw,"In one non-converged Kuramoto-based N=50, K=70 realization, CS skips persistently concentrate on one device over the final 50 cycles. The inset compares that device's intended skip times with the blocking transmission occupancy intervals.")


def validate(c:pd.DataFrame)->list[str]:
    checks=[]
    for (f,n),g in c.groupby(["coupling_function","device_count"]):
        q=g[g.mingap_convergence_rate_percent>=95].k.to_numpy(); row=g.iloc[0]; assert float(row.mingap_safe_k_ge95_lower)==float(q.min()) and float(row.mingap_safe_k_ge95_upper)==float(q.max()); checks.append(f"design-band PASS {f} N={n}")
    for (f,n),g in c.groupby(["coupling_function","device_count"]):
        assert float(g.loc[g.overall_per_percent_median.idxmin(),"k"])==float(g.per_optimal_k.iloc[0]); checks.append(f"PER optimum PASS {f} N={n}")
    for path in OUT.glob("*.pdf"):
        if path.stat().st_size<1000: raise AssertionError(path)
        if shutil.which("pdffonts"):
            text=subprocess.check_output(["pdffonts",str(path)],text=True); assert "yes" in text.lower(),f"font not embedded {path}"
        checks.append(f"PDF PASS {path.name}")
    return checks


def main()->int:
    OUT.mkdir(parents=True,exist_ok=True); c,r,_=source_frames()
    fig1_scatter(c);fig2_rates(c);fig3_fluctuation(c);fig4_design(c);fig5_speed(c);fig6_per(c);fig7_random();fig8_transient(r);fig9_n5(r);cs_report(r)
    (OUT/"figures_captions_draft.md").write_text("# Figure captions (draft)\n\n"+"\n\n".join(f"## {s}\n\n{t}" for s,t in CAPTIONS)+"\n",encoding="utf-8")
    checks=validate(c);(OUT/"execution.log").write_text("\n".join(checks)+"\n",encoding="utf-8")
    return 0


if __name__=="__main__": raise SystemExit(main())
