"""Fixed-anchor CSMA/CA smoke; run after tests/test_simulation3_csma_ca.py passes."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from research_program.analysis.n_sweep_metrics import censored_convergence_median
from research_program.simulation3.metrics import run_metrics
from research_program.simulation3.runner import Simulation3Request,run_simulation3_request
OUT=ROOT/'results/simulation3_csma_ca_smoke'; MASTER=json.loads((ROOT/'experiments/n_sweep_v3/initial_phase_master.json').read_text(encoding='utf-8'))['start_times_by_run']
NS=(5,10,20,50); RUNS=50; T=5000.; D=900000.; SETTINGS=((0,0.,0.),(3,10.,200.),(3,25.,200.),(3,50.,200.),(5,25.,200.))
def main():
 OUT.mkdir(parents=True,exist_ok=True); started=time.perf_counter(); rows=[]; log=OUT/'execution.log'; conditions=len(NS)*len(SETTINGS); log.write_text(f'start conditions={conditions} runs={conditions*RUNS}\n',encoding='utf-8')
 for n in NS:
  for retries,w0,wmax in SETTINGS:
   label=f'r_{retries}_w0_{w0:g}_wmax_{wmax:g}'; raw=OUT/'raw'/f'n_{n}'/label; starts=tuple(tuple(x[:n]) for x in MASTER[:RUNS])
   req=Simulation3Request('CSMA_CA',RUNS,20261990,0,T,D,0,n,raw,starts,carrier_sense_duration_ms=5,csma_w0_ms=w0,csma_w_max_ms=wmax,csma_max_retries=retries)
   if not (raw/f'csma_ca_{RUNS-1:04d}'/'send_log.csv').exists(): run_simulation3_request(req)
   for i in range(RUNS):
    anchors=pd.DataFrame({'time':np.concatenate([np.arange(float(s),D,T) for s in starts[i]]),'oscillator_id':np.concatenate([np.full(len(np.arange(float(s),D,T)),d) for d,s in enumerate(starts[i])])})
    rows.append(run_metrics(run_dir=raw/f'csma_ca_{i:04d}',run_index=i,device_count=n,cycle_time=T,duration=D,carrier_sense_duration_ms=5,airtime_ms=req.airtime_ms,coupling_parameter=0,window_mode='fixed_anchor',algorithm='CSMA_CA',extra={'csma_w0_ms':w0,'csma_w_max_ms':wmax,'csma_max_retries':retries},intended_anchor_df=anchors))
 df=pd.DataFrame(rows); df.to_csv(OUT/'run_metrics.csv',index=False); keys=['coupling_function','device_count','csma_w0_ms','csma_w_max_ms','csma_max_retries']
 cond=df.groupby(keys,as_index=False).agg(run_count=('run_id','count'),overall_per_percent_median=('overall_per_percent','median'),backoff_retry_total=('backoff_retry_total','sum'),retry_exhausted_abandon_total=('retry_exhausted_abandon_total','sum'),initial_20_cycle_loss_rate_percent_median=('initial_20_cycle_loss_rate_percent','median'),final_20_cycle_loss_rate_percent_median=('final_20_cycle_loss_rate_percent','median'),final_minus_initial_20_cycle_loss_rate_percent_median=('final_minus_initial_20_cycle_loss_rate_percent','median'),ttu_reach_rate_percent=('ttu_cycle',lambda x:100*x.notna().mean()),max_convergence_rate_percent=('max_converged',lambda x:100*x.mean()),mingap_convergence_rate_percent=('mingap_converged',lambda x:100*x.mean()),final_10_cycle_new_max_abs_dev_median=('final_10_cycle_new_max_abs_dev','median'),final_10_cycle_min_gap_median=('final_10_cycle_min_gap_median','median'))
 for prefix,flag in (('ttu','ttu_cycle'),('max_convergence','max_converged'),('mingap_convergence','mingap_converged')):
  v=df[flag] if prefix=='ttu' else df[f'{prefix}_cycle']; ok=df[flag].notna() if prefix=='ttu' else df[flag]
  med=df.assign(_v=v,_ok=ok).groupby(keys).apply(lambda g:censored_convergence_median(g._v,g._ok),include_groups=False).reset_index(name=f'{prefix}_cycle_censored_median'); cond=cond.merge(med,on=keys)
 cond.to_csv(OUT/'condition_metrics.csv',index=False)
 (OUT/'metadata.json').write_text(json.dumps({'schema_version':3,'experiment':'simulation3_csma_ca_smoke','algorithm':'CSMA_CA','condition_count':conditions,'runs_per_condition':RUNS,'cycle_time_ms':T,'duration_ms':D,'carrier_sense_duration_ms':5,'airtime_ms':20.544,'device_counts':list(NS),'settings':[{'max_retries':r,'w0_ms':w0,'w_max_ms':wm} for r,w0,wm in SETTINGS],'master_path':'experiments/n_sweep_v3/initial_phase_master.json','anchor_policy':'Fixed anchor: no backoff carry-over; next send is s_i+(k+1)T.','intended_time_policy':'Every fixed anchor is included in min-gap intended-time metrics; abandonment is logged at its original anchor.'},indent=2)+'\n',encoding='utf-8')
 with log.open('a',encoding='utf-8') as f:f.write(f'finished conditions={conditions} elapsed_sec={time.perf_counter()-started:.3f}\n')
if __name__=='__main__':main()
