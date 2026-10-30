"""Prosimos-only workaround: merge multiple end events into one via a converging exclusive gateway.
Semantically identical for simulation (an end event just terminates the case). Text-level edit on a COPY."""
import re, sys, xml.etree.ElementTree as ET
src,dst=sys.argv[1],sys.argv[2]; s=open(src,encoding='utf-8').read()
B={'b':'http://www.omg.org/spec/BPMN/20100524/MODEL'}
root=ET.parse(src).getroot(); proc=root.find('b:process',B)
ends=[e.get('id') for e in proc.findall('b:endEvent',B)]
if len(ends)<=1: open(dst,'w',encoding='utf-8').write(s); print("single end event, copied unchanged"); sys.exit()
flows={f.get('id'):(f.get('sourceRef'),f.get('targetRef')) for f in proc.findall('b:sequenceFlow',B)}
in_flows=[fid for fid,(a,b) in flows.items() if b in ends]
GW="gw_merged_end"; END="end_merged"; NEW="flow_merged_end"
# retarget incoming flows to the gateway
for fid in in_flows:
    s=re.sub(r'(<sequenceFlow [^>]*id="%s"[^>]*targetRef=")[^"]*(")'%re.escape(fid), r'\g<1>%s\2'%GW, s)
# remove old end events (with their <incoming> children)
for e in ends:
    s=re.sub(r'<endEvent [^>]*id="%s"[^>]*?(/>|>.*?</endEvent>)'%re.escape(e), '', s, flags=re.S)
# add gateway + single end + connecting flow before </process>
gw='<exclusiveGateway id="%s" gatewayDirection="Converging">%s<outgoing>%s</outgoing></exclusiveGateway>'%(GW,''.join('<incoming>%s</incoming>'%f for f in in_flows),NEW)
end='<endEvent id="%s"><incoming>%s</incoming></endEvent>'%(END,NEW)
fl='<sequenceFlow id="%s" sourceRef="%s" targetRef="%s" />'%(NEW,GW,END)
s=s.replace('</process>',gw+end+fl+'</process>',1)
open(dst,'w',encoding='utf-8').write(s)
r2=ET.parse(dst).getroot(); p2=r2.find('b:process',B)
print(f"merged {len(ends)} end events -> 1; end events now: {len(p2.findall('b:endEvent',B))}; flows: {len(flows)} -> {len(p2.findall('b:sequenceFlow',B))}")
