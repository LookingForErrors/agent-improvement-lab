"""Catalog curation, observed action evidence and public boundary contracts."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('catalog_builder_contract',ROOT/'web_demo/build_catalog.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=json.loads((ROOT/'web_demo/catalog-fixture.json').read_text())

    def test_curated_inventory_is_working_only_and_family_aware(self):
        data=builder.validate_catalog(copy.deepcopy(self.data))
        self.assertEqual(data['population'],{'scenarios':30,'families':30,'traces':180})
        self.assertEqual({case['split'] for case in data['cases']},{'working'})
        self.assertFalse(data['provenance']['heldout_raw_included'])
        self.assertEqual(data['provenance']['model_calls'],0);self.assertEqual(data['provenance']['human_labels'],0)
        for destination in builder.TEAMS:self.assertEqual(sum(case['intended_destination']==destination for case in data['cases']),6)
        self.assertEqual(sum(case['clarifications']==1 for case in data['cases']),8)
        self.assertEqual(sum(case['clarifications']==2 for case in data['cases']),6)
        self.assertEqual(sum(case['style']=='overloaded' and case['history_chars']>=5000 for case in data['cases']),5)

    def test_build_requires_explicit_source_hashes_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            selection=root/'selection.json'
            selection.write_text(json.dumps({'format':'routing-catalog-selection-v1','scope':'working_only'}))
            names=['tasks-working.jsonl','traces-working.jsonl','private/task-references.jsonl','private/trace-references.jsonl','rules.txt']
            files={}
            for name in names:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture\n')
                files[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            for name in names:
                for replacement in [None,{}, {'sha256':'not-a-digest'}]:
                    manifest=copy.deepcopy(files)
                    if replacement is None:manifest.pop(name)
                    else:manifest[name]=replacement
                    (root/'manifest.json').write_text(json.dumps({'files':manifest}))
                    with self.assertRaisesRegex(ValueError,'Missing or invalid source corpus SHA'):
                        builder.build(root,selection)
            (root/'manifest.json').write_text(json.dumps({'files':files}))
            (root/names[0]).write_text('changed fixture\n')
            with self.assertRaisesRegex(ValueError,'Changed source corpus'):
                builder.build(root,selection)

    def test_public_boundary_rejects_foreign_fields_origins_splits_and_broken_links(self):
        mutations=[lambda d:d.update(human_snapshot={'assessor':'private'}),lambda d:d['cases'][0].update(split='heldout'),lambda d:d['cases'][0]['traces'][0].update(provenance='live_model'),lambda d:d['cases'][0]['traces'][0]['messages'][0].update(annotator='private'),lambda d:d['cases'][0]['traces'][0]['messages'][2].update(tool_call_id='foreign'),lambda d:d['cases'][0]['traces'][0]['messages'][0].update(content='/Users/private/data'),lambda d:d['cases'][0].update(family_id=d['cases'][1]['family_id']),lambda d:d['cases'][0]['traces'][0]['messages'][2].update(content=json.dumps({'ticket_id':'x','status':'open','history':[], 'annotation':{'assessor':'private'}}))]
        for mutate in mutations:
            data=copy.deepcopy(self.data);mutate(data)
            with self.assertRaises(ValueError):builder.validate_catalog(data)

    def test_clarification_edits_preserve_turns_and_unsupported_claims_match_route(self):
        for case in self.data['cases']:
            success=case['traces'][0];users=[m['content'] for m in success['messages'] if m['role']=='user']
            self.assertEqual(len(users),case['input_user_turns']+case['clarifications'])
            if case['clarifications']==1:self.assertNotIn('The user ',users[-1])
            fabricated=next(t for t in case['traces'] if t['variant']=='fabricated_result')
            self.assertEqual(fabricated['messages'][-1]['content'],success['messages'][-1]['content']+' '+case['unsupported_claim'])
            self.assertNotIn('refunded your payment',fabricated['messages'][-1]['content'])

    def node_check(self,code):
        node=shutil.which('node');self.assertIsNotNone(node,'Node is required for catalog contracts')
        prefix="const fs=require('fs'),assert=require('node:assert/strict');const app=require(process.argv[1]),data=JSON.parse(fs.readFileSync(process.argv[2]));"
        result=subprocess.run([node,'-e',prefix+code,str(ROOT/'web_demo/catalog.js'),str(ROOT/'web_demo/catalog-fixture.json')],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_filters_search_full_context_and_count_scenarios_separately(self):
        self.node_check("""const all=app.filterCases(data,{query:'',variant:'',style:'',clarifications:''});assert.deepEqual([app.summarize(all).scenarios,app.summarize(all).families,app.summarize(all).traces],[30,30,180]);assert.equal(app.summarize(all,'success').traces,30);assert.equal(app.filterCases(data,{style:'overloaded'}).length,5);assert.equal(app.filterCases(data,{clarifications:'1'}).length,8);assert.equal(app.filterCases(data,{clarifications:'2'}).length,6);assert.equal(app.filterCases(data,{query:'nonexistent unique phrase'}).length,0);const long=all.find(c=>c.style==='overloaded'),context=JSON.parse(long.traces[0].messages.find(m=>m.function==='get_ticket').content);const token=context.history.find(r=>r.record_id.startsWith('archive')).text.match(/OLD-\\d+-\\d+/)[0];assert(app.filterCases(data,{query:token}).some(c=>c.id===long.id));assert.equal(app.filterCases(data,{variant:'tool_error_ignored',query:'MOCK_TIMEOUT'}).length,30);assert.equal(app.filterCases(data,{variant:'success',query:'MOCK_TIMEOUT'}).length,0);assert.equal(app.filterCases(data,{variant:'wrong_ticket',query:'EN-OTHER-999'}).length,30);assert(app.filterCases(data,{variant:'fabricated_result',query:'already reviewed your case'}).length>0);assert.throws(()=>app.filterCases(data,{style:'foreign'}));assert.throws(()=>app.filterCases(data,{variant:'foreign'}));assert.throws(()=>app.filterCases(data,{clarifications:'3'}));""")

    def test_recorded_evidence_and_exports_resolve_without_claiming_an_eval(self):
        self.node_check("""const before=JSON.stringify(data);for(const item of data.cases)for(const trace of item.traces){const e=app.evidence(item,trace);assert(trace.messages.some(m=>m.step_id===e.evidence_step));if(trace.variant==='missed_tool')assert.equal(e.action_destination,null);else if(trace.variant==='wrong_ticket')assert.notEqual(e.action_ticket,item.ticket_id);else if(trace.variant==='wrong_team')assert.notEqual(e.action_destination,item.intended_destination);else if(trace.variant==='tool_error_ignored')assert.equal(e.tool_status,'ERROR');else assert.equal(e.tool_status,'OK');const exported=app.traceExport(data,item,trace);assert.equal(exported.model_calls,0);assert.equal(exported.human_labels,0);assert.equal(exported.trace,trace);assert(!('judge' in exported)&&!('verdict' in exported)&&!('metrics' in exported));}assert.equal(JSON.stringify(data),before);assert.throws(()=>app.traceExport(data,structuredClone(data.cases[0]),data.cases[0].traces[0]));const item=data.cases[0],broken=structuredClone(item.traces[0]);broken.messages.find(m=>m.function==='route_ticket'&&m.role==='tool').content='{';assert.equal(app.evidence(item,broken).tool_status,'Not recorded');assert.equal(app.evidence(item,broken).tool_confirmed_assignment,false);const conflicting=structuredClone(item.traces[0]),result=conflicting.messages.find(m=>m.function==='route_ticket'&&m.role==='tool');result.content=JSON.stringify({...JSON.parse(result.content),ticket_id:'other-ticket'});assert.equal(app.evidence(item,conflicting).tool_confirmed_assignment,false);const unsupported=structuredClone(item.traces.find(t=>t.variant==='fabricated_result'));unsupported.messages.find(m=>m.function==='route_ticket'&&m.role==='tool').content='{';assert.equal(app.evidence(item,unsupported).tool_confirmed_assignment,false);assert(!app.evidence(item,unsupported).statement.startsWith('Routing is confirmed'));""")

    def test_action_groups_keep_every_original_step_in_order_and_require_matching_ids(self):
        self.node_check("""const before=JSON.stringify(data);for(const item of data.cases)for(const trace of item.traces){const groups=app.trajectoryGroups(trace);assert.deepEqual(groups.flatMap(g=>g.messages),trace.messages);for(const group of groups.filter(g=>g.kind==='action'))for(const response of group.messages.slice(1))assert(group.messages[0].tool_calls.some(call=>call.id===response.tool_call_id));}assert.equal(JSON.stringify(data),before);const trace=structuredClone(data.cases[0].traces[0]),callIndex=trace.messages.findIndex(m=>m.tool_calls?.length);trace.messages[callIndex].content='I will check this now.';assert(app.trajectoryGroups(trace).some(g=>g.messages[0].content==='I will check this now.'));trace.messages[callIndex+1].tool_call_id='unrelated';assert.deepEqual(app.trajectoryGroups(trace).flatMap(g=>g.messages),trace.messages);assert.equal(app.trajectoryGroups(trace).find(g=>g.messages[0]===trace.messages[callIndex]).messages.length,1);const delayed=structuredClone(data.cases[0].traces[0]);delayed.messages.splice(callIndex+1,0,{role:'assistant',content:'Intervening text',step_id:'delayed-text'});assert.deepEqual(app.trajectoryGroups(delayed).flatMap(g=>g.messages),delayed.messages);assert.equal(app.trajectoryGroups(delayed).find(g=>g.messages[0]===delayed.messages[callIndex]).messages.length,1);""")

    def test_compact_error_signals_preserve_tool_success_and_evidence_role(self):
        self.node_check("""for(const item of data.cases)for(const trace of item.traces){const signal=app.traceSignal(item,trace);assert.equal(signal.issue,trace.variant!=='success');assert(signal.steps.every(step=>trace.messages.some(m=>m.step_id===step)));assert(signal.steps.includes(app.evidence(item,trace).evidence_step));if(trace.variant==='tool_error_ignored')assert(signal.steps.includes(trace.messages.at(-1).step_id));if(trace.variant==='fabricated_result')assert.deepEqual(signal.steps,[trace.messages.at(-1).step_id]);if(['wrong_team','wrong_ticket'].includes(trace.variant)){const group=app.trajectoryGroups(trace).find(g=>g.kind==='action'&&g.messages[0].tool_calls.some(c=>c.function!=='get_ticket'));const call=group.messages[0].tool_calls[0];assert.equal(app.actionDisplay(call,group.messages[1]).status,'OK');assert.equal(signal.issue,true);}}const trace=data.cases[0].traces[0],group=app.trajectoryGroups(trace).find(g=>g.kind==='action'&&g.messages[0].tool_calls.some(c=>c.function==='route_ticket')),call=group.messages[0].tool_calls[0],result=group.messages[1];assert.equal(app.actionDisplay(call,{...result,content:'{malformed original payload'}).status,'Unconfirmed');assert.equal(app.actionDisplay(call,{...result,tool_call_id:'foreign'}).confirmed,false);assert.equal(app.actionDisplay(call,{...result,content:'null'}).confirmed,false);assert.equal(app.actionDisplay(call,{...result,content:JSON.stringify({...JSON.parse(result.content),assigned_to:'operator'})}).confirmed,false);""")


if __name__=='__main__':unittest.main()
