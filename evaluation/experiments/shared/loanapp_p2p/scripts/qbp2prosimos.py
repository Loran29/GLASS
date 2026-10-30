"""Convert a BIMP/QBP BPMN into a Prosimos JSON. Reuses Prosimos's own distribution mapping
(extract_dist_params) so parameter semantics are identical to the official converter."""
import json, xml.etree.ElementTree as ET
from prosimos.simulation_properties_parser import format_date
BIG=1e12
def extract_dist_params(d):
    """BIMP -> pix_framework/Prosimos 2.x format. Values in BIMP files are always seconds.
    EXPONENTIAL: arg1 is the mean (verified against the paper's ground-truth log medians)."""
    t=d.get('type').upper(); mean=float(d.get('mean')); a1=float(d.get('arg1'))
    V=lambda x:{'value':x}
    if t=='FIXED':       return {'distribution_name':'fix','distribution_params':[V(mean)]}
    if t=='EXPONENTIAL': return {'distribution_name':'expon','distribution_params':[V(a1),V(0.0),V(BIG)]}   # mean, min, max -> scale=mean
    if t=='NORMAL':      return {'distribution_name':'norm','distribution_params':[V(mean),V(a1),V(0.0),V(BIG)]}  # mean, std, min, max
    if t=='UNIFORM':     return {'distribution_name':'uniform','distribution_params':[V(a1),V(float(d.get('arg2')))]}
    raise ValueError('unsupported distribution '+t)
B={'b':'http://www.omg.org/spec/BPMN/20100524/MODEL'}; Q={'qbp':'http://www.qbp-simulator.com/Schema201212'}
def convert(bpmn_path, out_json):
    root=ET.parse(bpmn_path).getroot(); sim=root.find('.//qbp:processSimulationInfo',Q)
    proc=root.find('b:process',B)
    # 1. gateway branching (diverging = more than one outgoing flow; no reliance on gatewayDirection)
    branching={}; rev={}
    for tag in ('b:exclusiveGateway','b:inclusiveGateway'):
        for g in proc.findall(tag,B):
            outs=[o.text.strip() for o in g.findall('b:outgoing',B)]
            if len(outs)>1:
                branching[g.get('id')]={a:0.0 for a in outs}
                for a in outs: rev[a]=g.get('id')
    for fp in sim.find('qbp:sequenceFlows',Q).findall('qbp:sequenceFlow',Q):
        fid=fp.get('elementId')
        if fid in rev: branching[rev[fid]][fid]=float(fp.get('executionProbability'))
    for gid,arcs in branching.items():
        s=sum(arcs.values()); assert abs(s-1)<1e-6, f"gateway {gid} probabilities sum to {s}"
    # 2. calendars
    cal={}
    for t in sim.find('qbp:timetables',Q):
        cal[t.get('id')]=[{'from':r.get('fromWeekDay'),'to':r.get('toWeekDay'),'beginTime':format_date(r.get('fromTime')),'endTime':format_date(r.get('toTime'))} for r in t.find('qbp:rules',Q).findall('qbp:rule',Q)]
    # 3. arrival (plain or byTimetable)
    arr=sim.find('qbp:arrivalRateDistribution',Q); arrival_cal_id='QBP_DEFAULT_TIMETABLE'
    if arr.get('type') is None:                       # byTimetable variant (P2P)
        dists=arr.find('qbp:byTimetable',Q).findall('qbp:distribution',Q); assert len(dists)==1
        arrival_cal_id=dists[0].get('timetableId'); arr=dists[0]
    arrival=extract_dist_params(arr)
    arrival_cal=cal[[c for c in cal if 'ARRIVAL_CALENDAR' in c] [0]] if any('ARRIVAL_CALENDAR' in c for c in cal) else cal[arrival_cal_id]
    # 4. resources
    profiles={}; pools={}; rcal={}
    for r in sim.find('qbp:resources',Q):
        rid=r.get('id'); profiles[rid]={'name':r.get('name'),'resource_list':[]}; pools[rid]=[]
        for i in range(1,int(r.get('totalAmount'))+1):
            nid=f"{rid}_{i}"; pools[rid].append(nid); rcal[nid]=cal[r.get('timetableId')]
            profiles[rid]['resource_list'].append({'id':nid,'name':f"{r.get('name')}-{i:06d}",'cost_per_hour':r.get('costPerHour') or '0','amount':1})
    # 5. tasks and timers
    timer_ids={e.get('id') for e in proc.findall('b:intermediateCatchEvent',B)}
    task_res={}; events=[]
    for e in sim.find('qbp:elements',Q):
        eid=e.get('elementId'); dist=extract_dist_params(e.find('qbp:durationDistribution',Q))
        rids=e.find('qbp:resourceIds',Q)
        if eid in timer_ids or rids is None:
            assert eid in timer_ids, f"element {eid} has no resource but is not a timer"
            events.append({'event_id':eid,**dist})
        else:
            task_res[eid]={rp:dist for rp in pools[rids.find('qbp:resourceId',Q).text]}
    out={'resource_profiles':profiles,'arrival_time_distribution':arrival,'arrival_time_calendar':arrival_cal,
         'gateway_branching_probabilities':[{'gateway_id':g,'probabilities':[{'path_id':a,'value':v} for a,v in arcs.items()]} for g,arcs in branching.items()],
         'task_resource_distribution':[{'task_id':t,'resources':[{'resource_id':r,**d} for r,d in rs.items()]} for t,rs in task_res.items()],
         'resource_calendars':[{'id':f"{r}_cal",'name':f"{r}_cal",'time_periods':tp} for r,tp in rcal.items()],
         'event_distribution':events}
    # resource_profiles must be a list in Prosimos JSON; calendars referenced by id
    out['resource_profiles']=[{'id':rid,'name':p['name'],'resource_list':[{**x,'calendar':f"{x['id']}_cal"} for x in p['resource_list']]} for rid,p in profiles.items()]
    json.dump(out,open(out_json,'w'),indent=1)
    return dict(n_cases=sim.get('processInstances'),start=sim.get('startDateTime'),tasks=len(task_res),timers=len(events),gateways=len(branching),arrival=arrival,pools={p['name']:len(p['resource_list']) for p in out['resource_profiles']})
if __name__=="__main__":
    import sys; print(convert(sys.argv[1],sys.argv[2]))
