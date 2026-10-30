"""Check the manual Prosimos result folders (inputs/manual_models/<variant>/<model>/).

Per model: one JSON, one BPMN and 10 logs of 1000 cases with the expected columns; resource names in the logs match the
JSON's resource list and stay within each role's headcount; the JSON has one entry per person with an existing calendar.
Prints headcounts, roles whose calendar ends after 17:00, events per log, and the problems found.

Usage: python -I check_prosimos_results.py [path]
"""
import json, glob, os, csv, collections, sys
from pathlib import Path
root = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'inputs' / 'manual_models')
problems = []
rows = []
for d in sorted(glob.glob(os.path.join(root, '*', '*'))):
    if not os.path.isdir(d) or '_stats' in d: continue
    name = os.path.basename(d); var = os.path.basename(os.path.dirname(d))
    js = glob.glob(os.path.join(d, '*.json'))
    bp = glob.glob(os.path.join(d, '*.bpmn'))
    logs = sorted(glob.glob(os.path.join(d, 'seed_*_log.csv')))
    p = lambda m: problems.append(f'{var}/{name}: {m}')
    if len(js) != 1: p(f'json files {len(js)}'); continue
    if len(bp) != 1: p(f'bpmn files {len(bp)}')
    if len(logs) != 10: p(f'logs {len(logs)}')
    j = json.load(open(js[0], encoding='utf-8'))
    pers = {}; cal_of = {}
    for prof in j['resource_profiles']:
        for r in prof['resource_list']:
            if r.get('amount') != 1: p(f"amount!=1 for {r['id']}")
            if r['name'] != r['id']: p(f"name!=id for {r['id']}")
            pers[r['id']] = prof['name']; cal_of[r['id']] = r.get('calendar')
    cals = {c['id']: c for c in j['resource_calendars']}
    for rid, c in cal_of.items():
        if c not in cals: p(f'missing calendar {c}')
    td_ids = {x['resource_id'] for t in j['task_resource_distribution'] for x in t['resources']}
    if not td_ids <= set(pers): p(f'task dist resource ids not in profiles: {sorted(td_ids - set(pers))[:3]}')
    heads = collections.Counter(pers.values())
    # end hour per role
    endh = {}
    for rid, role in pers.items():
        tp = cals[cal_of[rid]]['time_periods']
        endh.setdefault(role, set()).add(max(x['endTime'][:5] for x in tp))
    seen_roles = collections.defaultdict(set); ncases = set(); nev = []
    bad_names = set(); cols_ok = True
    for f in logs:
        cs = set(); n = 0
        with open(f, newline='', encoding='utf-8') as fh:
            rd = csv.DictReader(fh)
            if rd.fieldnames != ['case_id','activity','enable_time','start_time','end_time','resource']: cols_ok = False
            for r in rd:
                cs.add(r['case_id']); n += 1
                res = r['resource']
                if res in pers: seen_roles[pers[res]].add(res)
                else: bad_names.add(res)
        ncases.add(len(cs)); nev.append(n)
    if not cols_ok: p('unexpected log columns')
    if ncases != {1000}: p(f'cases per log {sorted(ncases)}')
    if bad_names: p(f'resource names not in resource_list: {sorted(bad_names)[:5]}')
    for role, h in heads.items():
        if len(seen_roles.get(role, ())) > h: p(f'{role}: seen {len(seen_roles[role])} > headcount {h}')
    rows.append((var, name, dict(heads), {k: sorted(v) for k, v in endh.items() if v != {'17:00'}}, min(nev), max(nev), len(logs)))
for r in rows: print(r)
print('PROBLEMS:', len(problems))
for x in problems: print(' -', x)
