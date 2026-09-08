import subprocess
import sys
from pathlib import Path

import pytest

from orb import Params, resample, run_backtest
from orb.synthetic import make_synthetic_m1

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case,expected', [('match', 0), ('missing', 2), ('flipped', 2),
                                          ('no_python', 2), ('empty_ea', 2), ('duplicate', 1)])
def test_strict_reconciliation(tmp_path, case, expected):
    m1 = make_synthetic_m1(days=4, seed=4)
    bar_file = tmp_path / 'm1.csv'
    m1.to_csv(bar_file)
    result = run_backtest(resample(m1, 15), Params(atr_period=0), path=m1)
    ea = result.trades.copy().rename(columns={'r': 'r_multiple'})
    ea['commission'] = 0
    ea['swap'] = 0
    ea['date'] = ea.entry_time.dt.strftime('%Y.%m.%d')
    for column in ['signal_time', 'entry_time', 'exit_time']:
        ea[column] = ea[column].dt.strftime('%Y.%m.%d %H:%M:%S')
    ea['orb_time'] = ea.orb_time.dt.strftime('%Y.%m.%d %H:%M')
    ea = ea.drop(columns=['dir', 'risk_dist'])
    if case == 'missing':
        ea = ea.iloc[1:]
    elif case == 'flipped':
        ea.loc[0, 'reason'] = 'OTHER'
    elif case == 'empty_ea':
        ea = ea.iloc[:0]
    elif case == 'duplicate':
        import pandas as pd
        ea = pd.concat([ea, ea.iloc[:1]])
    log = tmp_path / 'ea.csv'
    log.write_text('# ORB EA v1.30 tf=PERIOD_M15 orb=16:30 atr_period=0\n' + ea.to_csv(index=False), encoding='utf-8')
    cmd = [sys.executable, str(ROOT / 'reconcile.py'), '--ea-log', str(log), '--bars', str(bar_file),
           '--m1', str(bar_file), '--strict', '--report', str(tmp_path / 'diff.csv')]
    if case == 'no_python':
        cmd += ['--set', 'vol_spike_min=10000']
    completed = subprocess.run(cmd, text=True, capture_output=True, cwd=tmp_path, timeout=30)
    assert completed.returncode == expected, completed.stdout + completed.stderr
    if case not in ('duplicate',):
        assert (tmp_path / 'diff.csv').exists()
