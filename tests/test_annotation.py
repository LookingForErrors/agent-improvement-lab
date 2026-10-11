"""Annotation reliability and blind API boundaries; isolated synthetic workspaces."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from agent_improvement_lab.annotation import (prepare, queue, case_data, save, public_export,
    export_private, connect, handler_for, Conflict, project_trace)
from agent_improvement_lab.schemas import Trace


def fixture():
    return {'id':'PRIVATE-BATCH:r2:trace','task_id':'PRIVATE-TASK','family_id':'PRIVATE-FAMILY',
        'split':'working','agent_version':'PRIVATE-VERSION','provenance':'PRIVATE-PROVENANCE',
        'messages':[
            {'step_id':'s0','role':'system','content':'SECRET-MODEL\nCurrent ticket: TICKET-1\nPRIVATE-PROMPT-VERSION'},
            {'step_id':'s1','role':'user','content':'I think my password is broken.'},
            {'step_id':'s2','role':'assistant','content':'I’ll check the ticket.'},
            {'step_id':'s3','role':'user','content':'Actually I need a receipt.'},
            {'step_id':'s4','role':'assistant','content':'','tool_calls':[{'id':'PRIVATE-CALL','name':'get_ticket','arguments':{'ticket_id':'TICKET-1'}}]},
            {'step_id':'s5','role':'tool','content':'{"status":"OK","history":[]}','tool_call_id':'PRIVATE-CALL','function':'get_ticket'},
            {'step_id':'s6','role':'assistant','content':'Which transaction?'},
            {'step_id':'s7','role':'user','content':'The newest one.'},
            {'step_id':'s8','role':'assistant','content':'Done.'}],
        'metadata':{'judge':'PRIVATE-JUDGE','reference':'PRIVATE-GOLD','usage':{'model':'SECRET-MODEL'},
          'message_sources':{'s0':None,'s1':'input','s2':'input','s3':'input','s4':None,'s5':None,'s6':None,'s7':'input','s8':None}}}


class AnnotationChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=self.root/'labels.sqlite3';self.traces=self.root/'traces.jsonl';self.rules=self.root/'rubric.txt'
        self.rules.write_text('Evaluate routing. Tool OK confirms only its reported action.')
        self.write([fixture()]);prepare(self.db,self.traces,self.rules);self.cid=queue(self.db)['cases'][0]['id']
    def tearDown(self):self.temp.cleanup()
    def write(self,rows):self.traces.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    def label(self,**overrides):
        data={'verdict':'incorrect','annotator':'QA only','evidence_steps':['m04'],
              'error_types':['wrong_route'],'comment':'Test evidence','scenario_problem':False,
              'scenario_evidence':[],'scenario_comment':'','expected_revision':0}
        data.update(overrides);return data
    def test_public_allowlist_origin_and_current_ticket(self):
        data=case_data(self.db,self.cid);serialized=json.dumps(public_export(self.db))
        for secret in ['PRIVATE','SECRET-MODEL','agent_version','source_hash','message_sources','judge','family_id','task_id','split']:
            self.assertNotIn(secret,serialized)
        self.assertEqual(data['current_ticket'],'TICKET-1')
        self.assertEqual([m['phase'] for m in data['messages'][:4]],['context']*3+['continuation'])
        self.assertTrue(data['messages'][1]['authored']);self.assertTrue(data['messages'][6]['authored'])
        self.assertEqual(data['messages'][6]['phase'],'continuation')
        self.assertEqual(data['messages'][3]['calls'][0]['id'],data['messages'][4]['result_for'])
    def test_durable_revisions_private_links(self):
        first=save(self.db,self.cid,self.label());self.assertEqual(first['revision'],1)
        second=save(self.db,self.cid,self.label(verdict='correct',error_types=[],expected_revision=1))
        self.assertEqual(second['revision'],2);self.assertEqual(case_data(self.db,self.cid)['annotation']['verdict'],'correct')
        with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],2)
        private=export_private(self.db)[0];self.assertEqual(private['original_evidence_steps'],['s4'])
        self.assertEqual(private['trace_id'],fixture()['id']);self.assertEqual(private['assessor_kind'],'human')
    def test_conflict_never_overwrites(self):
        save(self.db,self.cid,self.label())
        with self.assertRaises(Conflict):save(self.db,self.cid,self.label(verdict='correct',error_types=[]))
        self.assertEqual(case_data(self.db,self.cid)['annotation']['verdict'],'incorrect')
    def test_context_cannot_be_current_agent_error(self):
        for evidence in [['m02'],['m05'],['m99']]:
            with self.subTest(evidence=evidence),self.assertRaises(ValueError):save(self.db,self.cid,self.label(evidence_steps=evidence))
        self.assertIsNone(case_data(self.db,self.cid)['annotation'])
    def test_user_evidence_roundtrip_with_assistant_anchor(self):
        saved = save(self.db, self.cid, self.label(evidence_steps=['m01', 'm07', 'm04']))
        self.assertEqual(saved['evidence_steps'], ['m01', 'm07', 'm04'])
        self.assertEqual(export_private(self.db)[0]['original_evidence_steps'], ['s1', 's7', 's4'])
        self.assertFalse(saved['scenario_problem'])
        self.assertEqual(saved['scenario_evidence'], [])
        self.assertEqual(case_data(self.db, self.cid)['annotation']['evidence_steps'], saved['evidence_steps'])

    def test_user_evidence_alone_cannot_blame_user_for_assistant_error(self):
        for evidence in [['m01'], ['m07'], ['m01', 'm07'], ['m07', 'm02']]:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                save(self.db, self.cid, self.label(evidence_steps=evidence))
        self.assertIsNone(case_data(self.db, self.cid)['annotation'])

    def test_scenario_issue_separate_and_unassessable_explanation(self):
        data=self.label(verdict='correct',error_types=[],evidence_steps=[],scenario_problem=True,scenario_evidence=['m02'],scenario_comment='Authored prelude issue')
        self.assertEqual(save(self.db,self.cid,data)['verdict'],'correct')
        with self.assertRaises(ValueError):save(self.db,self.cid,self.label(verdict='unassessable',comment='',expected_revision=1))
    def test_non_error_verdict_cannot_retain_hidden_error_types(self):
        for verdict in ['correct', 'unassessable']:
            with self.subTest(verdict=verdict), self.assertRaises(ValueError):
                save(self.db, self.cid, self.label(verdict=verdict))
        self.assertIsNone(case_data(self.db, self.cid)['annotation'])
        saved = save(self.db, self.cid, self.label(verdict='unassessable', error_types=[]))
        self.assertEqual(saved['error_types'], [])

    def test_reorder_refresh_and_mutation_are_safe(self):
        save(self.db,self.cid,self.label());before=public_export(self.db)
        prepare(self.db,self.traces,self.rules);self.assertEqual(public_export(self.db),before)
        changed=fixture();changed['messages'][-1]['content']='Different output';self.write([changed])
        with self.assertRaises(ValueError):prepare(self.db,self.traces,self.rules)
        self.assertEqual(public_export(self.db),before)
        self.rules.write_text('Changed rubric')
        with self.assertRaises(ValueError):prepare(self.db,self.traces,self.rules)
        self.assertEqual(public_export(self.db),before)
    def test_strict_labels_and_reasoning_projection(self):
        for override in [{'unexpected':'PRIVATE-VERSION'},{'expected_revision':True},{'error_types':['invented']},{'evidence_steps':['m04','m04']}]:
            with self.subTest(override=override),self.assertRaises(ValueError):save(self.db,self.cid,self.label(**override))
        f=fixture();f['messages'][-1]['content']='';f['messages'][-1]['reasoning']='Observed reasoning'
        p,_=project_trace(Trace.model_validate(f));self.assertEqual(p['messages'][-1]['reasoning'],'Observed reasoning')
    def test_partial_refresh_rollback(self):
        second=fixture();second['id']='another';self.write([fixture(),second]);prepare(self.db,self.traces,self.rules)
        before=public_export(self.db);changed=fixture();changed['messages'][1]['content']='changed'
        new=fixture();new['id']='third';self.write([new,changed])
        with self.assertRaises(ValueError):prepare(self.db,self.traces,self.rules)
        self.assertEqual(public_export(self.db),before)
    def test_missing_source_fails_closed_and_generic_trace_is_blind(self):
        for f in [fixture(),fixture()]:
            if f['metadata'].get('message_sources'):f['metadata'].pop('message_sources')
            with self.assertRaises(ValueError):project_trace(Trace.model_validate(f))
        f=fixture();f['messages'][0]['content']='No ticket identity'
        public,_=project_trace(Trace.model_validate(f))
        self.assertNotIn('current_ticket',public)
        self.assertNotIn('No ticket identity',json.dumps(public))


class AnnotationHTTPChecks(unittest.TestCase):
    setUp = AnnotationChecks.setUp
    tearDown = AnnotationChecks.tearDown
    write = AnnotationChecks.write
    label = AnnotationChecks.label
    def test_routes_blinding_origin_save_and_restart(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler)
        port=server.server_address[1];server.RequestHandlerClass=handler_for(self.db,port)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body=None,headers=None):
            actual={'Content-Type':'application/json',**(headers or {})}
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode() if body else None,headers=actual)
            try:
                with urllib.request.urlopen(req,timeout=3) as r:return r.status,r.read().decode()
            except urllib.error.HTTPError as e:return e.code,e.read().decode()
        try:
            status,session=request('/api/session');token=json.loads(session)['token'];self.assertEqual(status,200)
            path='/api/cases/'+self.cid+'/annotation'
            self.assertEqual(request(path,self.label())[0],403)
            self.assertEqual(request(path,self.label(),{'X-Annotation-Token':token,'Origin':'https://example.com'})[0],403)
            self.assertEqual(request('/api/queue',headers={'Host':'malicious.local'})[0],403)
            for forbidden in ['/labels.sqlite3','/../annotation.py','/api/private','/api/cases/'+self.cid+'/raw']:
                self.assertEqual(request(forbidden)[0],404)
            self.assertEqual(request(path,self.label(),{'X-Annotation-Token':token})[0],200)
            self.assertEqual(request(path,self.label(),{'X-Annotation-Token':token})[0],409)
            for endpoint in ['/api/queue','/api/cases/'+self.cid,'/api/export']:
                status,body=request(endpoint);self.assertEqual(status,200);self.assertNotIn('PRIVATE',body)
        finally:server.shutdown();server.server_close();thread.join()
        # New handler/process session reads the same durable database.
        self.assertEqual(case_data(self.db,self.cid)['annotation']['revision'],1)

if __name__=='__main__':unittest.main()
