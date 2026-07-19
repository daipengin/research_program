"""Regression gate: simulation3 K/L must reproduce the v3 raw send streams."""
from __future__ import annotations
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from research_program.simulation3.runner import Simulation3Request, run_simulation3_request

ROOT = Path(__file__).resolve().parents[1]
RAW = Path(os.environ.get('N_SWEEP_V3_RAW_ROOT', r'F:\research_program_data\n_sweep_v3_raw'))
MASTER = json.loads((ROOT / 'experiments/n_sweep_v3/initial_phase_master.json').read_text(encoding='utf-8'))['start_times_by_run']


@unittest.skipUnless(RAW.exists(), 'n_sweep_v3 raw data drive is unavailable')
class Simulation3V3EquivalenceTest(unittest.TestCase):
    cases = (('KURAMOTO', 50, 500), ('LINEAR', 50, 30), ('KURAMOTO', 5, 10))

    def test_send_times_match_v3_raw_for_three_runs(self):
        for algorithm, n, k in self.cases:
            with self.subTest(algorithm=algorithm, n=n, k=k), tempfile.TemporaryDirectory() as tmp:
                starts = tuple(tuple(row[:n]) for row in MASTER[:3])
                results = run_simulation3_request(Simulation3Request(
                    algorithm, 3, 20_261_990, k, 5_000, 900_000, .25, n,
                    Path(tmp), starts, carrier_sense_duration_ms=5,
                ))
                db = RAW / algorithm.lower() / f'n_{n}' / f'k_{k}.sqlite'
                with sqlite3.connect(db) as conn:
                    for result in results:
                        index = int(result['random_run_index'])
                        run_id = conn.execute('SELECT run_id FROM runs WHERE random_run_index=?', (index,)).fetchone()[0]
                        expected = pd.read_sql_query('SELECT time FROM send_log WHERE run_id=? ORDER BY rowid', conn, params=(run_id,)).time.to_numpy(float)
                        actual = pd.read_csv(Path(str(result['output_dir'])) / 'send_log.csv').time.to_numpy(float)
                        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-9)
