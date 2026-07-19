from __future__ import annotations
import tempfile
import unittest
import json
from pathlib import Path
import pandas as pd
import numpy as np
from research_program.analysis.calculate_phase_gap_error import compute_mean_abs_gap_error_per_cycle
from research_program.io.send_log import add_detection_time_column
from research_program.simulation3.runner import Simulation3Request, _CSMACAEngine, run_simulation3_request
from research_program.analysis.n_sweep_metrics import first_consecutive_above

class _MaximumBackoff:
    def uniform(self, _low, high): return high

class CSMACATest(unittest.TestCase):
    def request(self, root: Path, **kw):
        return Simulation3Request('CSMA_CA', 1, 7, 0, kw.pop('T',100), kw.pop('duration',300), 0, kw.pop('N',2), root, kw.pop('starts',((0,15),)), carrier_sense_duration_ms=kw.pop('cs',5), csma_w0_ms=kw.pop('w0',10), csma_w_max_ms=kw.pop('wmax',40), csma_max_retries=kw.pop('retries',0), **kw)
    def execute(self, **kw):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        result=run_simulation3_request(self.request(Path(tmp.name),**kw))[0]; folder=Path(str(result['output_dir']))
        return pd.read_csv(folder/'send_log.csv'),pd.read_csv(folder/'carrier_sense_log.csv')
    def test_r_zero_is_one_shot_cs_skip(self):
        _,cs=self.execute(retries=0); self.assertIn('skip_busy_exhausted',set(cs.action)); self.assertNotIn('backoff_retry',set(cs.action))
    def test_busy_retries_and_doubles_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=_CSMACAEngine(self.request(Path(tmp),N=1,duration=80,starts=((15,),),retries=2,w0=10,wmax=40), (15,), Path(tmp), 0)
            engine.intervals.append((0.0,100.0,99)); engine.rng=_MaximumBackoff(); engine.run()
            self.assertEqual(engine.retry_total,2); self.assertEqual(engine.abandon_total,1)
            self.assertEqual(engine.backoff_width_history,[10,20])
    def test_retry_crossing_next_cycle_boundary_is_abandoned(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=_CSMACAEngine(self.request(Path(tmp),T=100,duration=180,starts=((70,75),),w0=100,wmax=100,retries=3), (70,75), Path(tmp), 0); engine.rng=_MaximumBackoff(); engine.run()
            self.assertGreaterEqual(engine.abandon_total,1)
    def test_abandon_uses_original_anchor_as_intended_time(self):
        _,cs=self.execute(retries=0); skipped=cs[cs.action=='skip_busy_exhausted']; self.assertTrue((skipped.time==15).any())
        send=add_detection_time_column(pd.DataFrame({'time':[0.,20.], 'transmission_end_time':[0.,20.], 'oscillator_id':[0,2], 'send_count':[1,1]}))
        phase=compute_mean_abs_gap_error_per_cycle(send_df=send,cycle_starts=np.array([0.,100.]),num_devices=2,nominal_cycle_time_ms=100,carrier_sense_df=skipped)
        self.assertTrue(np.isfinite(phase.iloc[0].min_gap_rad))
    def test_next_cycle_remains_fixed_anchor_after_backoff(self):
        _,cs=self.execute(retries=3,w0=20,wmax=20)
        # The fixed next-cycle anchor is attempted at 15+100 even if it is busy again.
        self.assertIn(115.0,cs[cs.action=='backoff_retry'].time.to_numpy(float))

    def test_n50_fixed_anchors_keep_min_gap_and_do_not_converge(self):
        root=Path(__file__).resolve().parents[1]
        starts=json.loads((root/'experiments/n_sweep_v3/initial_phase_master.json').read_text(encoding='utf-8'))['start_times_by_run'][0][:50]
        anchors=pd.DataFrame({'time':np.concatenate([np.asarray(starts,dtype=float)+k*5000 for k in range(11)]), 'oscillator_id':np.tile(np.arange(50),11), 'action':'skip_busy'})
        send=add_detection_time_column(pd.DataFrame({'time':[], 'transmission_end_time':[], 'oscillator_id':[], 'send_count':[]}))
        phase=compute_mean_abs_gap_error_per_cycle(send_df=send,cycle_starts=np.arange(0.,55000.,5000.),num_devices=50,nominal_cycle_time_ms=5000,carrier_sense_df=anchors)
        self.assertEqual(phase['min_gap_rad'].nunique(),1)
        self.assertLess(float(phase['min_gap_rad'].iloc[0]),2*np.pi*25.544/5000)
        self.assertIsNone(first_consecutive_above(cycle_indices=phase.cycle_index.to_numpy(),values=phase.min_gap_rad.to_numpy(),threshold=2*np.pi*25.544/5000))
