from __future__ import annotations
import math
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from research_program.simulation3.pco_d import calculate_new_remaining_ms
from research_program.simulation3.runner import Simulation3Request, run_simulation3_request
from research_program.analysis.calculate_phase_gap_error import compute_mean_abs_gap_error_per_cycle
from research_program.io.send_log import add_detection_time_column


class PCODSimulation3Test(unittest.TestCase):
    def request(self, root: Path, **kw):
        return Simulation3Request('PCO_D', 1, 20260717, kw.pop('alpha', .5), kw.pop('T', 100.),
            kw.pop('duration', 300.), kw.pop('r', .2), kw.pop('N', 2), root,
            kw.pop('starts', ((0, 15),)), carrier_sense_duration_ms=kw.pop('cs', 5), **kw)
    def execute(self, **kw):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); root=Path(tmp.name)
        result=run_simulation3_request(self.request(root, **kw))[0]
        return pd.read_csv(Path(result['output_dir'])/'send_log.csv'), pd.read_csv(Path(result['output_dir'])/'carrier_sense_log.csv')
    def test_formula(self): self.assertEqual(calculate_new_remaining_ms(remaining_ms=4,listening_ratio=.25,cycle_time_ms=40,alpha=.5),7)
    def test_airtime_uses_simulation1_lora_parameters(self): self.assertAlmostEqual(self.request(Path('.')).airtime_ms,20.544)
    def test_listen_precedes_send(self):
        log,_=self.execute(T=100,r=.2,starts=((0,60),)); self.assertAlmostEqual(log.iloc[0].time,20)
    def test_receive_postpones_pending_send(self):
        log,_=self.execute(T=100,r=.2,alpha=.5,starts=((0,15),)); self.assertGreater(log[log.oscillator_id==1].iloc[0].time,35)
    def test_each_receive_revises_pending_send(self):
        log,_=self.execute(T=100,r=.2,alpha=.5,N=3,starts=((0,5,10),))
        # Obsolete revision events cannot create an additional physical send.
        self.assertFalse(log.duplicated(subset=['time', 'oscillator_id']).any())
    def test_busy_cs_skips_and_records_schema(self):
        _,cs=self.execute(T=100,r=.2,starts=((0,15),)); self.assertIn('skip_busy',set(cs.action)); self.assertTrue({'carrier_sense_start','blocking_transmission_end'}.issubset(cs.columns))
    def test_simultaneous_starts_are_counted_as_collisions(self):
        log,_=self.execute(N=2,starts=((0,0),),cs=0); log=add_detection_time_column(log)
        phase=compute_mean_abs_gap_error_per_cycle(send_df=log,cycle_starts=__import__('numpy').array([0.]),num_devices=2,nominal_cycle_time_ms=100)
        self.assertEqual(phase.iloc[0].simultaneous_collision_count,2)


class IntendedGapSimulation3Test(unittest.TestCase):
    def test_skip_busy_is_an_intended_send_for_min_gap(self):
        send=add_detection_time_column(pd.DataFrame({'time':[1000.,2000.,3000.,4000.], 'transmission_end_time':[1000.]*4, 'oscillator_id':[0,2,3,4], 'send_count':[1]*4}))
        cs=pd.DataFrame({'time':[1003.], 'oscillator_id':[1], 'action':['skip_busy']})
        value=compute_mean_abs_gap_error_per_cycle(send_df=send,cycle_starts=__import__('numpy').array([0.,5000.]),num_devices=5,nominal_cycle_time_ms=5000,carrier_sense_df=cs).iloc[0].min_gap_rad
        self.assertAlmostEqual(value,2*math.pi*3/5000,places=12)
