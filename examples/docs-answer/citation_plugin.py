"""Trusted demo plugin: citation-to-observed-document linkage, not semantic truth."""
import json
import re
from agent_improvement_lab.criteria import Decision

PLUGIN_API_VERSION=1

def check(trace,parameters):
    if parameters:raise ValueError('This check takes no options')
    final=next((m for m in reversed(trace.messages) if m.role=='assistant' and not m.tool_calls),None)
    if final is None or not isinstance(final.content,str) or not final.content.strip():
        return Decision(status='not_applicable',explanation='No textual final answer to link.')
    read_calls={c.id:c for m in trace.messages for c in m.tool_calls if c.name=='read_doc'}
    seen={};malformed=[]
    for m in trace.messages[:trace.messages.index(final)]:
        if m.role!='tool' or m.tool_call_id not in read_calls:continue
        try:
            payload=json.loads(m.content) if isinstance(m.content,str) else m.content
            if not isinstance(payload,dict) or type(payload.get('ok')) is not bool:raise ValueError('malformed read response')
            if payload['ok']:
                doc=payload.get('doc_id')
                if not isinstance(doc,str) or not isinstance(payload.get('text'),str):raise ValueError('missing observed document')
                seen[doc]=m.step_id
        except (ValueError,TypeError):malformed.append(m.step_id)
    if malformed:
        return Decision(verdict='unassessable',evidence_steps=malformed,explanation='Malformed read evidence prevents citation verification.')
    cites=re.findall(r'\[([A-Za-z0-9_-]+)\]',final.content)
    if not cites or any(c not in seen for c in cites):
        return Decision(verdict='incorrect',evidence_steps=[final.step_id],explanation='Missing citation or source not successfully read before this answer.')
    evidence=list(dict.fromkeys([final.step_id]+[seen[c] for c in cites]))
    return Decision(verdict='correct',evidence_steps=evidence,explanation='Citations link to observed earlier successful reads. This does not validate the claim or version relevance.')

def register(registry):registry.register('citation_sources','1',check)
