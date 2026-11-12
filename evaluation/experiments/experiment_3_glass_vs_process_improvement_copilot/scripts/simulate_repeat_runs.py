"""Simulate the repeat-run manual models with Prosimos 2.1.0, same settings as the run-1 models.

1000 cases from 2023-02-09T10:00:00+02:00, seeds 42-51; random and NumPy seeded before each run.
Logs are written next to each model (stats in prosimos_models/_stats/); existing logs are skipped unless --force.

Usage: python scripts/simulate_repeat_runs.py [--runs 1-5] [--seeds 42-51] [--cases 1000] [--workers 6] [--only SUBSTR] [--force]
"""
import argparse
import os
import random
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

RR = Path(__file__).resolve().parents[1] / 'inputs' / 'repeat_runs'
START_AT = '2023-02-09T10:00:00+02:00'


def rng(text):
    lo, _, hi = text.partition('-')
    return list(range(int(lo), int(hi or lo) + 1))


def simulate(model_dir, seeds, cases, force):
    import numpy as np
    from prosimos.simulation_engine import run_simulation
    from prosimos.warning_logger import warning_logger
    model_dir = Path(model_dir)
    name, variant, root = model_dir.name, model_dir.parent.name, model_dir.parents[1]
    stats_dir = root / '_stats' / variant / name
    stats_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for seed in seeds:
        log = model_dir / f'seed_{seed}_log.csv'
        if log.exists() and not force:
            out.append(('SKIP', seed, 0.0))
            continue
        warning_logger.clear_warnings()
        random.seed(seed)
        np.random.seed(seed)
        t0 = time.time()
        try:
            run_simulation(str(model_dir / f'{name}.bpmn'), str(model_dir / f'{name}.json'), cases,
                           str(stats_dir / f'seed_{seed}_stats.csv'), str(log), START_AT, False)
            os.replace(stats_dir / 'simulation_warnings.txt', model_dir / 'simulation_warnings.txt')
            out.append(('OK', seed, time.time() - t0))
        except Exception:
            out.append(('FAIL', seed, traceback.format_exc()[-500:]))
    return str(model_dir.relative_to(RR)), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default='1-5')
    ap.add_argument('--seeds', default='42-51')
    ap.add_argument('--cases', type=int, default=1000)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--only', default='')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    dirs = [d for n in rng(a.runs) for d in sorted((RR / f'run_{n}' / 'prosimos_models').glob('*/*'))
            if d.is_dir() and d.parent.name != '_stats' and a.only in str(d)]
    print(f'{len(dirs)} models x {len(rng(a.seeds))} seeds, {a.cases} cases, start {START_AT}', flush=True)
    fails = []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(simulate, str(d), rng(a.seeds), a.cases, a.force) for d in dirs]
        for f in as_completed(futs):
            name, res = f.result()
            ok = sum(r[0] == 'OK' for r in res)
            skip = sum(r[0] == 'SKIP' for r in res)
            bad = [r for r in res if r[0] == 'FAIL']
            fails += [(name, r[1], r[2]) for r in bad]
            print(f'{name}: {ok} simulated, {skip} skipped, {len(bad)} failed', flush=True)
    print(f'done, {len(fails)} failures')
    for name, seed, err in fails:
        print('FAIL', name, seed, err)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
