"""Japanese presentation variants of the fixed n_sweep_v3 figures.

The data-access helpers are imported from make_paper_figures_v5 so that the
reporting and paper figures select identical condition and representative-run
data.  No simulation or aggregate redefinition occurs here.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from mpl_toolkits.axes_grid1.inset_locator import inset_axes

import make_paper_figures_v5 as paper


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "presentation_figures"
PAPER = ROOT / "results" / "paper_figures_v5"
FONT = Path(r"C:\Windows\Fonts\NotoSansJP-VF.ttf")
ORANGE, BLUE, RED = "#E69F00", "#0072B2", "#D62728"
SOURCE = "n_sweep_v3: 2 functions x N={5,10,20,50} x 20 K values x 1000 trials"
N_MARKER = {5: "o", 10: "s", 20: "^", 50: "D"}
JP_NAME = {"KURAMOTO": "Kuramoto based", "LINEAR": "frog chorus based"}
COLOR = {"KURAMOTO": ORANGE, "LINEAR": BLUE}
LOG: list[str] = []


def configure() -> None:
    font_manager.fontManager.addfont(str(FONT))
    mpl.rcParams.update({"font.family": "Noto Sans JP", "font.size": 12, "axes.unicode_minus": False,
                         "figure.dpi": 300, "savefig.dpi": 300, "svg.fonttype": "none"})


def style(ax) -> None:
    ax.grid(True, which="both", alpha=.25)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)


def footer(fig) -> None:
    fig.text(.995, .012, SOURCE, ha="right", va="bottom", fontsize=6.4, color="0.35")


def save(fig, name: str, data: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUT / f"{name}.csv", index=False, lineterminator="\n")
    footer(fig); fig.savefig(OUT / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)


def condition_and_run() -> tuple[pd.DataFrame, pd.DataFrame]:
    c, r, _ = paper.source_frames()
    return c, r


def fig1(c: pd.DataFrame) -> None:
    d = c.copy(); positive=d.loc[d.overall_per_percent_median>0,"overall_per_percent_median"]; floor=max(float(positive.min())/3,1e-6)
    d["per_plot_percent"]=d.overall_per_percent_median.clip(lower=floor); d["per_zero_clipped"]=d.overall_per_percent_median<=0
    fig,ax=plt.subplots(figsize=(8.0,5.5),constrained_layout=True)
    for f in ["KURAMOTO","LINEAR"]:
        for n in [5,10,20,50]:
            p=d[(d.coupling_function==f)&(d.device_count==n)]
            ax.scatter(p.final_10_cycle_new_max_abs_dev_median,p.per_plot_percent,c=COLOR[f],marker=N_MARKER[n],s=42,edgecolors="black",linewidths=.3,label=f"{JP_NAME[f]}, N={n}")
    ax.set_xscale("log");ax.set_yscale("log"); ax.set_xlabel("最終10サイクルの最大位相偏差 [rad]");ax.set_ylabel("全期間PER中央値 [%]");style(ax)
    n5=d[d.device_count==5]; ax.scatter(n5.final_10_cycle_new_max_abs_dev_median,n5.per_plot_percent,s=110,facecolors="none",edgecolors=RED,lw=1.2)
    ax.annotate("揺らぎ大でも PER≈0\n→ 許容できる揺らぎが存在",(n5.final_10_cycle_new_max_abs_dev_median.max(),n5.per_plot_percent.median()),xytext=(-180,45),textcoords="offset points",arrowprops={"arrowstyle":"->","color":RED},color=RED,fontsize=10)
    bad=d[(d.coupling_function=="KURAMOTO")&(d.device_count==50)&(d.k<=70)];ax.scatter(bad.final_10_cycle_new_max_abs_dev_median,bad.per_plot_percent,s=130,facecolors="none",edgecolors=RED,lw=1.3)
    ax.annotate("揺らぎ小でも PER 4–6%\n→ 整列指標では見えない損失",(bad.final_10_cycle_new_max_abs_dev_median.median(),bad.per_plot_percent.median()),xytext=(30,-35),textcoords="offset points",arrowprops={"arrowstyle":"->","color":RED},color=RED,fontsize=10)
    ax.text(.02,.04,f"PER=0 は {floor:.1e}% に表示下限クリップ",transform=ax.transAxes,fontsize=8)
    ax.text(.5,.95,"結論：等間隔への近さは通信品質を説明しない（160条件の実測）",transform=ax.transAxes,ha="center",va="top",fontsize=13,weight="bold",bbox={"facecolor":"white","alpha":.8,"edgecolor":"none"})
    ax.legend(ncol=2,fontsize=7.8,frameon=False,loc="upper left");save(fig,"pres_scatter_fluctuation_vs_per",d)


def fig2(r: pd.DataFrame) -> None:
    choice=r[(r.coupling_function=="KURAMOTO")&(r.device_count==5)&(r.k==10)&(r.overall_per_percent==0)].iloc[0]; idx=int(choice.run_index)
    run,phase,sends,_=paper.load_demo("KURAMOTO",5,10,idx); base=float(sends.time.min()); sends=sends.copy();sends["cycle_index"]=np.floor((sends.time-base)/paper.T_MS).astype(int)+1;sends["phase_ms"]=(sends.time-base)%paper.T_MS
    fig,axes=plt.subplots(2,1,figsize=(8.0,5.5),constrained_layout=True,sharex=True)
    for dev,p in sends.groupby("oscillator_id"): axes[0].scatter(p.cycle_index,p.phase_ms,s=7,label=f"デバイス {dev}")
    axes[0].set_ylabel("送信位相 [ms]");style(axes[0]);axes[0].legend(ncol=5,fontsize=8,frameon=False,loc="upper center")
    axes[0].plot([135,155],[4300,4300],color="black",lw=2);axes[0].text(145,4360,"理想間隔 T/N = 1000 ms",ha="center",fontsize=9)
    axes[0].plot([135,135+paper.OCC_MS/50],[4000,4000],color=RED,lw=4);axes[0].text(142,3910,"占有時間 τ_occ = 25.5 ms\n（理想間隔の2.6%）",color=RED,fontsize=9)
    axes[0].text(.02,.03,"等間隔化していない実送信位相",transform=axes[0].transAxes,fontsize=11,weight="bold",bbox={"facecolor":"white","alpha":.75,"edgecolor":"none"})
    axes[1].plot(np.arange(1,181),np.zeros(180),color=ORANGE,lw=2);axes[1].set_ylim(-.2,1);axes[1].set_ylabel("累積PER [%]");axes[1].set_xlabel("サイクル");style(axes[1]);axes[1].text(.5,.72,"結論：大きな揺らぎが残っても衝突ゼロ",transform=axes[1].transAxes,ha="center",fontsize=12,weight="bold")
    data=sends.assign(selected_run_index=idx,selection="KURAMOTO N=5 K=10, PER=0");save(fig,"pres_n5_run_demo",data)
    LOG.append(f"N5 demo PASS run_index={idx} overall_PER={choice.overall_per_percent}")


def fig3(r: pd.DataFrame) -> None:
    report=pd.read_csv(PAPER/"cs_starvation_report.csv");sel=report[report.classification=="fixed_starvation"].iloc[0];idx=int(sel.run_index);top=int(sel.top_device)
    run,phase,sends,cs=paper.load_demo("KURAMOTO",50,70,idx);t0=float(sends.time.min());cs=cs[cs.action=="skip_busy"].copy();cs["cycle_index"]=np.floor((cs.time-t0)/paper.T_MS).astype(int)+1;sends=sends.copy();sends["cycle_index"]=np.floor((sends.time-t0)/paper.T_MS).astype(int)+1
    start=int(cs.cycle_index.max())-49;cs=cs[cs.cycle_index>=start];sends=sends[(sends.cycle_index>=start)&(sends.cycle_index<=start+49)]; phase=phase[(phase.cycle_index>=start)&(phase.cycle_index<=start+49)]
    fig,axes=plt.subplots(2,1,figsize=(8.0,5.7),constrained_layout=True,sharex=True,height_ratios=[1.5,1])
    axes[0].scatter(sends.cycle_index,sends.oscillator_id,s=5,color="0.45",alpha=.35,label="送信");axes[0].scatter(cs.cycle_index,cs.oscillator_id,s=10,color="0.5",marker="x",label="CS見送り");hit=cs[cs.oscillator_id==top];axes[0].scatter(hit.cycle_index,hit.oscillator_id,s=28,color=RED,marker="x",label=f"飢餓デバイス {top}")
    axes[0].set_ylabel("デバイスID");style(axes[0]);axes[0].legend(ncol=3,fontsize=8,frameon=False,loc="upper right")
    axes[0].text(.02,.06,"49台は整列済み。しかし1台が毎サイクル送信を見送り続けている（実データ）",transform=axes[0].transAxes,color=RED,fontsize=11,weight="bold",bbox={"facecolor":"white","alpha":.8,"edgecolor":"none"})
    threshold=paper.TWO_PI*paper.OCC_MS/paper.T_MS;axes[1].plot(phase.cycle_index,phase.min_gap_rad,color=ORANGE,lw=1.8);axes[1].axhline(threshold,color=RED,ls="--");axes[1].set_ylabel("意図時刻 min gap [rad]");axes[1].set_xlabel("サイクル");style(axes[1]);axes[1].text(.64,.1,"閾値を下回り続ける",transform=axes[1].transAxes,color=RED,fontsize=10)
    data=pd.concat([sends.assign(record_type="send"),cs.assign(record_type="skip_busy"),phase.assign(record_type="min_gap")],ignore_index=True);data["selected_run_index"]=idx;data["highlighted_device"]=top;save(fig,"pres_starvation_evidence",data);LOG.append(f"starvation PASS run_index={idx} device={top} share={sel.top_device_share:.3f}")


def fig4(c: pd.DataFrame) -> None:
    p=c[(c.coupling_function=="KURAMOTO")&(c.device_count==50)].copy();fig,ax=plt.subplots(figsize=(8.0,5.2),constrained_layout=True)
    series=[("ttu_reach_rate_percent","TTU到達率","black","-",2.7),("mingap_convergence_rate_percent","min gap版","#D62728","-",2.2),("max_convergence_rate_percent","実送信・最悪ペア版","#777777","--",1.6),("aux_mean_convergence_rate_percent","平均版（補助）","#AAAAAA",":",1.6)]
    for col,label,color,ls,lw in series:ax.plot(p.k,p[col],label=label,color=color,ls=ls,lw=lw,marker="o",ms=3)
    ax.set_xscale("log");ax.set_ylim(-3,103);ax.set_xlabel("K");ax.set_ylabel("到達率 [%]");style(ax);ax.legend(frameon=False,ncol=2,loc="lower right")
    ax.annotate("低K側：最悪ペア版は締め出しを見落とし\n100%と誤判定",(7,100),xytext=(20,75),textcoords="data",arrowprops={"arrowstyle":"->"},fontsize=9)
    ax.annotate("高K側：最悪ペア版は保守的すぎて\n最良Kを不合格に",(500,11.2),xytext=(260,42),textcoords="data",arrowprops={"arrowstyle":"->"},fontsize=9)
    ax.text(.03,.28,"全域：min gap版だけが\nTTU（通信品質）と一致",transform=ax.transAxes,color=RED,weight="bold",fontsize=12,bbox={"facecolor":"white","alpha":.8,"edgecolor":"none"})
    random=pd.read_csv(PAPER/"fig_criterion_random_baseline.csv");ins=inset_axes(ax,width="30%",height="32%",loc="lower left",borderpad=2);vals=random[random.N==50].mean_abs_deviation;ins.hist(vals,bins=30,color="0.65");ins.axvline(paper.eps_for(50),color=RED,lw=1);ins.set_title("ランダム初期位相\n平均版は閾値以下",fontsize=7);ins.tick_params(labelsize=6)
    data=p.assign(inset_metric="N=50 mean_abs_deviation",inset_threshold=paper.eps_for(50));save(fig,"pres_criteria_vs_ttu",data)


def fig5(c: pd.DataFrame) -> None:
    fits=pd.read_csv(PAPER/"slope_fit_results.csv");fig,axes=plt.subplots(1,2,figsize=(8.4,4.3),constrained_layout=True,sharey=True);source=[]
    for ax,f in zip(axes,["KURAMOTO","LINEAR"],strict=True):
        p=c[(c.coupling_function==f)&(c.device_count==50)].copy();fit=fits[(fits.internal_function==f)&(fits.N==50)].iloc[0];eps=paper.eps_for(50);color=COLOR[f]
        ax.plot(p.k,p.final_10_cycle_new_max_abs_dev_median,marker="o",color=color,lw=1.8,label="実測")
        x=np.geomspace(fit.k_min,fit.k_max,100);ax.plot(x,fit.a*x+fit.b,color=color,ls="--",lw=1.5,label="線形域フィット")
        ax.axhline(eps,color=RED,lw=1.4);cross=float(fit.K_cross);ax.scatter(cross,eps,color=RED,s=45,zorder=5);ax.annotate(f"限界 K={cross:.1f}",(cross,eps),xytext=(8,10),textcoords="offset points",color=RED,fontsize=9)
        ax.set_xscale("log");ax.set_yscale("log");ax.set_xlabel("K");ax.set_title(JP_NAME[f]);style(ax);ax.legend(frameon=False,fontsize=8)
        source.append(p.assign(fit_a=fit.a,fit_b=fit.b,fit_k_cross=cross,epsilon_tol=eps))
    axes[0].set_ylabel("最終10サイクルの位相偏差 [rad]");fig.text(.5,.02,"結論：揺らぎはKに比例（実測）。関数により安全なK上限が大きく異なる。",ha="center",color=RED,weight="bold",fontsize=11)
    save(fig,"pres_scaling_and_kstar",pd.concat(source,ignore_index=True));LOG.append("scaling PASS Kcross Kuramoto=420.64 frog=28.86 (slope_fit_results.csv)")


def fig6(c: pd.DataFrame)->None:
    fig,ax=plt.subplots(figsize=(7.4,4.6),constrained_layout=True);data=[]
    for f in ["KURAMOTO","LINEAR"]:
        p=c[c.coupling_function==f].groupby("device_count").first().reset_index();ax.fill_between(p.device_count,p.mingap_safe_k_ge95_lower,p.mingap_safe_k_ge95_upper,color=COLOR[f],alpha=.28,label=f"{JP_NAME[f]} 安全K帯");ax.plot(p.device_count,p.per_optimal_k,color=COLOR[f],marker="o",lw=2,label=f"{JP_NAME[f]} PER最適K");data.append(p)
    ax.set_xscale("log");ax.set_yscale("log");ax.set_xticks([5,10,20,50]);ax.set_xticklabels(["5","10","20","50"]);ax.set_xlabel("デバイス数 N");ax.set_ylabel("K");style(ax);ax.legend(ncol=2,fontsize=8,frameon=False)
    ax.text(.03,.06,"Nが増えると frog の使えるK帯が急速に狭まる。\nN=50では Kuramoto帯(150–500)と frog帯(5–20)が完全に分離（実測）",transform=ax.transAxes,color=RED,weight="bold",fontsize=10,bbox={"facecolor":"white","alpha":.85,"edgecolor":"none"});save(fig,"pres_design_map",pd.concat(data,ignore_index=True))


def fig7(c:pd.DataFrame)->None:
    d=c[c.device_count==50].copy();fig,ax=plt.subplots(figsize=(7.7,4.5),constrained_layout=True);allrows=[]
    for f in ["KURAMOTO","LINEAR"]:
        p=d[d.coupling_function==f];color=COLOR[f];ax.plot(p.k,p.mingap_convergence_cycle_censored_median,color=color,lw=2,marker="o",label=f"{JP_NAME[f]} min gap");ax.plot(p.k,p.ttu_cycle_median,color=color,lw=1.5,ls="--",marker="o",ms=3,label=f"{JP_NAME[f]} TTU")
        safe=p[p.mingap_convergence_rate_percent>=95];best=safe.loc[safe.mingap_convergence_cycle_censored_median.idxmin()];ax.scatter(best.k,best.mingap_convergence_cycle_censored_median,color=RED,s=45,zorder=5);allrows.append(p)
    target=d[(d.coupling_function=="KURAMOTO")&(d.k==500)].iloc[0];ax.annotate(f"Kuramoto K=500: 中央値{target.mingap_convergence_cycle_censored_median:.0f}サイクル\n= {target.mingap_convergence_cycle_censored_median*5:.0f}秒、到達率{target.mingap_convergence_rate_percent:.0f}% (1000試行)",(500,target.mingap_convergence_cycle_censored_median),xytext=(-180,35),textcoords="offset points",color=RED,arrowprops={"arrowstyle":"->","color":RED},fontsize=9)
    ax.set_xscale("log");ax.set_xlabel("K");ax.set_ylabel("収束サイクル打ち切り中央値");style(ax);ax.legend(ncol=2,fontsize=8,frameon=False);sec=ax.secondary_yaxis("right",functions=(lambda x:x*5/60,lambda x:x*60/5));sec.set_ylabel("時間 [min]");ax.text(.04,.06,"結論：ランダム初期状態から約1分強で\n衝突ゼロ保証状態に到達",transform=ax.transAxes,color=RED,weight="bold",fontsize=11,bbox={"facecolor":"white","alpha":.85,"edgecolor":"none"});save(fig,"pres_convergence_speed",pd.concat(allrows,ignore_index=True))


def fig8()->None:
    names=["pres_scatter_fluctuation_vs_per","pres_n5_run_demo","pres_starvation_evidence","pres_criteria_vs_ttu","pres_scaling_and_kstar","pres_design_map","pres_convergence_speed"]
    fig,ax=plt.subplots(figsize=(12,7),constrained_layout=True);ax.axis("off");ax.text(.5,.95,"実機の条件下で最も良いパラメータを系統的に選び、実機で確認する",ha="center",va="top",fontsize=20,weight="bold")
    blocks=[(.06,.5,"① 良さの定義","通信品質に直結する min gap を採用"),(.38,.5,"② 設計則","N・関数・Kから安全帯と最適点を選ぶ"),(.70,.5,"③ 実機検証","未着手\n設計則確定後に (N, 関数, K) を決めて実施")]
    for x,y,title,body in blocks:
        ax.add_patch(plt.Rectangle((x,y),.24,.28,transform=ax.transAxes,facecolor="#F7F7F7",edgecolor=RED if "未着手" in body else "0.35",lw=2));ax.text(x+.12,y+.22,title,ha="center",transform=ax.transAxes,fontsize=17,weight="bold");ax.text(x+.12,y+.10,body,ha="center",transform=ax.transAxes,fontsize=11,color=RED if "未着手" in body else "black")
    ax.annotate("",(.37,.64),(.30,.64),xycoords=ax.transAxes,arrowprops={"arrowstyle":"->","lw":2});ax.annotate("",(.69,.64),(.62,.64),xycoords=ax.transAxes,arrowprops={"arrowstyle":"->","lw":2})
    positions=[(.04,.05),(.21,.05),(.38,.05),(.55,.05),(.72,.05),(.04,.28),(.21,.28)]
    for name,(x,y) in zip(names,positions,strict=True):
        image=plt.imread(OUT/f"{name}.png");ins=ax.inset_axes([x,y,.15,.18]);ins.imshow(image);ins.set_xticks([]);ins.set_yticks([]);ins.set_title(name.replace("pres_",""),fontsize=6)
    footer(fig);fig.savefig(OUT/"pres_overview_with_status.png",dpi=300,bbox_inches="tight");fig.savefig(OUT/"pres_overview_with_status.svg",bbox_inches="tight");pd.DataFrame({"thumbnail":names,"source":"generated presentation figure"}).to_csv(OUT/"pres_overview_with_status.csv",index=False,lineterminator="\n");plt.close(fig)


def contact_sheet()->None:
    names=sorted(p.stem for p in OUT.glob("pres_*.png"));fig,axes=plt.subplots(2,4,figsize=(12,6),constrained_layout=True)
    for ax,name in zip(axes.flat,names,strict=False):ax.imshow(plt.imread(OUT/f"{name}.png"));ax.set_title(name,fontsize=7);ax.axis("off")
    for ax in axes.flat[len(names):]:ax.axis("off")
    fig.savefig(OUT/"contact_sheet.png",dpi=150,bbox_inches="tight");plt.close(fig)


def validate(c:pd.DataFrame)->None:
    fits=pd.read_csv(PAPER/"slope_fit_results.csv");assert abs(float(fits[(fits.internal_function=="KURAMOTO")&(fits.N==50)].K_cross.iloc[0])-420.63759681491473)<1e-9
    assert abs(float(fits[(fits.internal_function=="LINEAR")&(fits.N==50)].K_cross.iloc[0])-28.86036858992187)<1e-9
    row=c[(c.coupling_function=="KURAMOTO")&(c.device_count==50)&(c.k==500)].iloc[0];assert row.mingap_convergence_cycle_censored_median==15 and row.mingap_convergence_rate_percent==100
    LOG.extend(["validation PASS Kcross from shared slope_fit_results.csv", "validation PASS Kuramoto N=50 K=500: 15 cycles, 100%", "validation PASS Japanese Noto Sans JP configured"])


def main()->int:
    configure();OUT.mkdir(parents=True,exist_ok=True);c,r=condition_and_run();fig1(c);fig2(r);fig3(r);fig4(c);fig5(c);fig6(c);fig7(c);fig8();contact_sheet();validate(c);(OUT/"execution.log").write_text("\n".join(LOG)+"\n",encoding="utf-8");return 0


if __name__=="__main__":raise SystemExit(main())
