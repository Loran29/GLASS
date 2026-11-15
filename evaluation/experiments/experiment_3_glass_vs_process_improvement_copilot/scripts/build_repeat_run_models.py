"""Build the manual BPMN models and Prosimos JSON files for Copilot runs 1-5.

Levers (as for the run-1 models): extended hours = named roles move to a 09:00-19:00 timetable;
extra resources = +EXTRA people per named role. Then merge_ends.py and qbp2prosimos.py.

  jobs   (openpyxl)  applicability/applicability_run_N.xlsx -> run_N/jobs.csv
  build  (prosimos)  run_N/jobs.csv -> run_N/bps_models_improved/ and run_N/prosimos_models/

Usage: python scripts/build_repeat_run_models.py {jobs|build} [N ...]   (default: all runs)
"""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
RR = EXP / 'inputs' / 'repeat_runs'
SHARED = EXP.parent / 'shared' / 'loanapp_p2p'
SRC_MODELS = SHARED / 'bps_models'
CONVERTERS = SHARED / 'scripts'
EXTRA = 2  # people added per role (staffing limit is +3)

ROLES = {'LoanApp': ['Clerk', 'Loan Officer', 'AML Investigator', 'Appraiser', 'Senior Officer'],
         'Procure2Pay': ['Financial Manager', 'Purchasing Agent', 'Requester', 'Requester Manager']}
# Ideas that name no role: the role performing the G2 target activity.
ANALYST_CHOICE = {'LoanApp_G2': ['Loan Officer'], 'LoanApp_G2G3': ['Loan Officer'], 'Procure2Pay_G2': ['Supplier']}
JOB_COLS = ['config', 'source_model', 'lever', 'output_model', 'roles', 'role_source', 'idea', 'inefficiency']

EXT_TT_ID = 'qbp_ext_hours_09_19'
EXT_TT = (f'<qbp:timetable id="{EXT_TT_ID}" name="Business Hours - Extended (09-19)">'
          '<qbp:rules id="qbp_ext_hours_rules"><qbp:rule id="qbp_ext_hours_rule" name="Timeslot" '
          'fromTime="09:00:00.000+00:00" toTime="19:00:00.000+00:00" '
          'fromWeekDay="MONDAY" toWeekDay="FRIDAY" /></qbp:rules></qbp:timetable>')


def source_model(cfg):
    process, goals = cfg.split('_')
    return {'G1': f'{process}_durations', 'G2': f'{process}_extraneous',
            'G3': f'{process}_resource_target'}.get(goals, cfg)


# ---------------------------------------------------------------- step 1: jobs
def make_jobs(n):
    import openpyxl
    ws = openpyxl.load_workbook(RR / 'applicability' / f'applicability_run_{n}.xlsx', read_only=True)[
        'Directly applicable ideas']
    rows = list(ws.iter_rows(values_only=True))
    head = rows[0]
    jobs, seen = [], {}
    for r in rows[1:]:
        x = dict(zip(head, r))
        cfg = x['Configuration']
        lever = 'hours' if x['Change in the BPMN'] == 'More working hours' else 'resources'
        how = x['How to apply in the simulation']
        m = re.search(r'(?:Extend the working hours of|Add people to): (.*?)\.(?:\n|$)', how)
        named = re.sub(r'\s*\(from motivation/impact\)', '', m.group(1)) if m else ''
        roles = [b for b in ROLES[cfg.split('_')[0]] if re.search(r'\b%s\b' % re.escape(b), named, re.I)]
        if roles:
            src = 'named in the idea title' if x['Which role is changed?'].startswith('Yes') else \
                'named in the motivation / impact text'
        else:
            roles, src = ANALYST_CHOICE[cfg], 'not named in the idea: analyst choice (as in run 1)'
        seen.setdefault((cfg, lever), []).append(len(jobs))
        jobs.append(dict(config=cfg, source_model=source_model(cfg), lever=lever, roles='; '.join(roles),
                         role_source=src, idea=x['Idea'], inefficiency=x['Inefficiency']))
    for (cfg, lever), idx in seen.items():  # same lever twice in one config -> _1, _2
        for k, i in enumerate(idx, 1):
            tag = lever if len(idx) == 1 else f'{lever}_{k}'
            folder = 'extended_hours' if lever == 'hours' else 'extra_resources'
            jobs[i]['output_model'] = f'{folder}/{cfg}__{tag}'
    out = RR / f'run_{n}' / 'jobs.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, JOB_COLS)
        w.writeheader()
        w.writerows(jobs)
    print(f'run_{n}: {len(jobs)} directly applicable ideas -> {out.relative_to(EXP)}')


# ---------------------------------------------------------------- step 2: build
def apply(xml, lever, roles, log):
    for role in roles:
        m = re.search(r'(<qbp:resource id="[^"]+" name="%s" totalAmount=")(\d+)("[^>]*?timetableId=")([^"]+)"'
                      % re.escape(role), xml)
        assert m, f'resource {role} not found'
        if lever == 'resources':
            old = int(m.group(2))
            xml = xml[:m.start(2)] + str(old + EXTRA) + xml[m.end(2):]
            log.append((role, 'totalAmount', old, old + EXTRA))
        else:
            xml = xml[:m.start(4)] + EXT_TT_ID + xml[m.end(4):]
            log.append((role, 'timetable/toTime', '17:00', '19:00'))
    if lever == 'hours':
        assert '</qbp:timetables>' in xml
        xml = xml.replace('</qbp:timetables>', EXT_TT + '</qbp:timetables>', 1)
    return xml


def build(n):
    sys.path.insert(0, str(CONVERTERS))
    from qbp2prosimos import convert
    run = RR / f'run_{n}'
    jobs = list(csv.DictReader(open(run / 'jobs.csv', encoding='utf-8')))
    changes = []
    for j in jobs:
        xml = (SRC_MODELS / f"{j['source_model']}.bpmn").read_text(encoding='utf-8')
        log = []
        xml = apply(xml, j['lever'], j['roles'].split('; '), log)
        bimp = run / 'bps_models_improved' / f"{j['output_model']}.bpmn"
        bimp.parent.mkdir(parents=True, exist_ok=True)
        bimp.write_text(xml, encoding='utf-8')
        for role, attr, old, new in log:
            changes.append([j['config'], j['source_model'] + '.bpmn', j['output_model'] + '.bpmn', j['idea'], j['lever'],
                            role, attr, old, new, j['role_source']])
        name = Path(j['output_model']).name
        dest = run / 'prosimos_models' / Path(j['output_model']).parent.name / name
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(CONVERTERS / 'merge_ends.py'), str(bimp), str(dest / f'{name}.bpmn')],
                       check=True, capture_output=True)
        convert(str(dest / f'{name}.bpmn'), str(dest / f'{name}.json'))
        js = json.load(open(dest / f'{name}.json', encoding='utf-8'))
        for p in js['resource_profiles']:
            for r in p['resource_list']:
                r['name'] = r['id']
        json.dump(js, open(dest / f'{name}.json', 'w', encoding='utf-8'), indent=1)
    with open(run / 'bps_models_improved' / 'change_log.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['config', 'source_model', 'output_model', 'idea', 'lever', 'role', 'attribute', 'before', 'after',
                    'role_source'])
        w.writerows(changes)
    print(f'run_{n}: {len(jobs)} models, {len(changes)} changes -> {run.relative_to(EXP)}')


if __name__ == '__main__':
    step, runs = sys.argv[1], [int(x) for x in sys.argv[2:]] or [1, 2, 3, 4, 5]
    for n in runs:
        {'jobs': make_jobs, 'build': build}[step](n)
