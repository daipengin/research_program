"""Run the requested simulation3 PCO-D smoke after test gates have passed."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'src'))
from research_program.simulation3.runner import Simulation3Request, run_simulation3_request
from research_program.simulation3.metrics import run_metrics

ALPHAS=(.05,.1,.2,.3,.5,.7,.9); NS=(5,10,20,50); RUNS=50; T=5000.; DURATION=900000.
MASTER=json.loads((ROOT/'experiments/n_sweep_v3/initial_phase_master.json').read_text(encoding='utf-8'))['start_times_by_run']
OUT=ROOT/'results/simulation3_pco_d_smoke'
def main():
    OUT.mkdir(parents=True,exist_ok=True); started=time.perf_counter(); rows=[]; log=OUT/'execution.log'
    conditions=len(ALPHAS)*len(NS)*2; log.write_text(f'start conditions={conditions} runs={conditions*RUNS}\n',encoding='utf-8')
    for mode in ('fixed_r_025','original_r_1_over_n'):
      for n in NS:
       for alpha in ALPHAS:
        raw=OUT/'raw'/mode/f'n_{n}'/f'alpha_{alpha:g}'
        if (raw/f'pco_d_{RUNS-1:04d}'/'send_log.csv').exists(): continue
        r=.25 if mode=='fixed_r_025' else 1/n
        req=Simulation3Request('PCO_D',RUNS,20261990,alpha,T,DURATION,r,n,raw,
          tuple(tuple(x[:n]) for x in MASTER[:RUNS]),carrier_sense_duration_ms=5)
        run_simulation3_request(req)
        for i in range(RUNS): rows.append(run_metrics(run_dir=raw/f'pco_d_{i:04d}',run_index=i,device_count=n,cycle_time=T,duration=DURATION,carrier_sense_duration_ms=5,airtime_ms=req.airtime_ms,coupling_parameter=alpha,window_mode=mode))
    df=pd.DataFrame(rows); df.to_csv(OUT/'run_metrics.csv',index=False)
    cond=df.groupby(['coupling_function','coupling_parameter','alpha','window_mode','device_count'],as_index=False).agg(run_count=('run_id','count'),overall_per_percent_median=('overall_per_percent','median'),ttu_reach_rate_percent=('ttu_cycle',lambda x:100*x.notna().mean()),max_convergence_rate_percent=('max_converged',lambda x:100*x.mean()),mingap_convergence_rate_percent=('mingap_converged',lambda x:100*x.mean()))
    cond.to_csv(OUT/'condition_metrics.csv',index=False)
    (OUT/'metadata.json').write_text(json.dumps({'schema_version':3,'algorithm':'PCO_D','coupling_parameter_interpretation':'alpha','window_modes':['fixed_r_025','original_r_1_over_n'],'condition_count':conditions,'runs_per_condition':RUNS,'elapsed_seconds':time.perf_counter()-started},indent=2)+'\n',encoding='utf-8')
    with log.open('a',encoding='utf-8') as f:f.write(f'finished conditions={conditions} elapsed_sec={time.perf_counter()-started:.3f}\n')
if __name__=='__main__': main()
