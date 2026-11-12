"""Read the simulation parameters embedded in a BIMP/QBP (Apromore) BPMN file.

The LoanApp and Procure2Pay models keep both the process structure and the
simulation parameters (arrivals, resources, timetables, activity durations,
branching probabilities) in one BPMN file, under qbp:processSimulationInfo.
"""
import json
import sys
import xml.etree.ElementTree as ET

NS = {'b': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
      'q': 'http://www.qbp-simulator.com/Schema201212'}


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def distribution(d):
    """Summarise a qbp distribution element.

    Values in the XML are in seconds. The effective mean follows the BIMP
    convention: FIXED/NORMAL use 'mean', EXPONENTIAL uses 'arg1' as its mean
    (timers in Procure2Pay_extraneous store mean=NaN, arg1=value).
    """
    if d is None:
        return None
    t = d.get('type')
    m, a1, a2 = _float(d.get('mean')), _float(d.get('arg1')), _float(d.get('arg2'))
    unit = d.findtext('q:timeUnit', namespaces=NS) or 'seconds'
    eff = {'FIXED': m, 'NORMAL': m, 'EXPONENTIAL': a1,
           'UNIFORM': (a1 + a2) / 2 if a1 is not None and a2 is not None else None,
           'TRIANGULAR': ((a1 or 0) + (m or 0) + (a2 or 0)) / 3}.get(t, m)
    return dict(type=t, mean=m, arg1=a1, arg2=a2, unit=unit,
                eff_mean_min=None if eff is None or eff != eff else round(eff / 60, 3),
                txt=f"{t}(mean={d.get('mean')},arg1={d.get('arg1')},arg2={d.get('arg2')}) "
                    f"[ui:{d.get('rawMean')}/{d.get('rawArg1')} {unit}]")


def parse(path):
    r = ET.parse(path).getroot()
    p = r.find('b:process', NS)
    names, kinds = {}, {}
    for el in p:
        tag = el.tag.split('}')[1]
        if tag in ('sequenceFlow', 'extensionElements'):
            continue
        names[el.get('id')] = (el.get('name') or '').strip() or el.get('id')
        kinds[el.get('id')] = tag
    flows = {f.get('id'): (f.get('sourceRef'), f.get('targetRef')) for f in p.findall('b:sequenceFlow', NS)}

    si = r.find('.//q:processSimulationInfo', NS)
    ar = si.find('q:arrivalRateDistribution', NS)
    if ar.get('type') is None:  # Procure2Pay nests it under qbp:byTimetable
        ar = ar.find('.//q:distribution', NS)

    res = {x.get('id'): dict(name=x.get('name'), amount=int(x.get('totalAmount')), tt=x.get('timetableId'))
           for x in si.iter('{%s}resource' % NS['q'])}
    tts = {t.get('name'): [f"{x.get('fromWeekDay')}-{x.get('toWeekDay')} {x.get('fromTime')[:5]}-{x.get('toTime')[:5]}"
                           for x in t.iter('{%s}rule' % NS['q'])]
           for t in si.iter('{%s}timetable' % NS['q'])}

    elems = {}
    for e in si.find('q:elements', NS):
        eid = e.get('elementId')
        rid = [x.text for x in e.iter('{%s}resourceId' % NS['q'])]
        elems[eid] = dict(kind=kinds.get(eid), name=names.get(eid, eid),
                          dur=distribution(e.find('q:durationDistribution', NS)),
                          res=[res[x]['name'] if x in res else x for x in rid])

    counts = {}
    for k in kinds.values():
        counts[k] = counts.get(k, 0) + 1

    return dict(arrival=distribution(ar),
                resources={v['name']: v['amount'] for v in res.values()},
                res_tt={v['name']: v['tt'] for v in res.values()},
                timetables=tts, elems=elems, counts=counts, nflows=len(flows))


if __name__ == '__main__':
    print(json.dumps(parse(sys.argv[1]), indent=1, default=str))
