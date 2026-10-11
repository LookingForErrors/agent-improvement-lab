"""Import correctness and corruption checks; no judgments about agent quality."""
import copy
import io
from contextlib import redirect_stdout
import json
import tempfile
import unittest
from pathlib import Path
from pydantic import ValidationError
from agent_improvement_lab.import_traces import Mapping,import_file,load_mapping,main
from agent_improvement_lab.schemas import Assessment,Trace

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'tests/fixtures/import/vendor.json'
MAP=ROOT/'mappings/vendor-example.yaml'

class ImportChecks(unittest.TestCase):
    def fixture(self):return json.loads(VENDOR.read_text())[0]
    def import_record(self,record,mapping=None):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'records.json';path.write_text(json.dumps([record],ensure_ascii=False))
            return import_file(path,mapping or load_mapping(MAP))
    def test_json_jsonl_order_reasoning_and_source_preservation(self):
        a=import_file(VENDOR,MAP);b=import_file(VENDOR.with_suffix('.jsonl'),MAP)
        self.assertTrue(a.ok);self.assertTrue(b.ok)
        x=a.records[0].model_dump(exclude={'source'});y=b.records[0].model_dump(exclude={'source'})
        self.assertEqual(x,y);trace=a.records[0]
        self.assertEqual([m.step_id for m in trace.messages],[f'e{i}' for i in range(6)])
        self.assertEqual(trace.messages[1].reasoning,'Observed optional reasoning text, not inferred.')
        self.assertEqual(trace.messages[1].tool_calls[0].arguments,{'ticket_id':'DEMO-1'})
        self.assertEqual(trace.source.record,self.fixture())
        self.assertEqual(trace.metadata,{'batch':0,'approved':False,'optional':None})
        self.assertEqual(trace.task.objective,'I was charged twice for one order.')
    def test_bad_links_names_duplicates_and_future_calls_rejected(self):
        mutations=[('result_id',lambda r:r['events'][2]['result'].update(call_id='not-called')),
            ('name',lambda r:r['events'][2]['result'].update(name='different_tool')),
            ('step_id',lambda r:r['events'][5].update(event_id='e0')),
            ('call_id',lambda r:r['events'][3]['actions'][0].update(call_id='call-lookup')),
            ('duplicate_result',lambda r:r['events'].append(copy.deepcopy(r['events'][2]))),
            ('future_call',lambda r:r['events'].__setitem__(slice(1,3),[r['events'][2],r['events'][1]]))]
        for name,mutate in mutations:
            with self.subTest(name=name):
                record=self.fixture();mutate(record);result=self.import_record(record)
                self.assertFalse(result.ok);self.assertEqual(result.records,[])
                self.assertTrue(all(d.severity=='error' for d in result.diagnostics))
    def test_missing_result_is_warning_not_agent_failure(self):
        record=self.fixture();record['events']=record['events'][:2]
        result=self.import_record(record)
        self.assertTrue(result.ok);self.assertEqual(len(result.records),1)
        self.assertEqual(result.diagnostics[0].code,'unanswered_tool_call')
        self.assertFalse(hasattr(result.records[0],'verdict'))
    def test_missing_and_null_are_distinct(self):
        record=self.fixture();del record['events'][0]['body']
        result=self.import_record(record);self.assertFalse(result.ok)
        self.assertEqual(result.diagnostics[0].path,'events[0].body')
        record['events'][0]['body']=None;self.assertTrue(self.import_record(record).ok)
        record['events'][0]['event_id']=None;self.assertFalse(self.import_record(record).ok)
    def test_string_arguments_must_be_object_json(self):
        for value in ['not json','[]','null','{"x":1,"x":2}','NaN']:
            with self.subTest(value=value):
                record=self.fixture();record['events'][1]['actions'][0]['function']['arguments']=value
                self.assertFalse(self.import_record(record).ok)
    def test_strict_mapping_and_roles(self):
        config=load_mapping(MAP).model_dump()
        for mutate in [lambda c:c.update(transform='eval'),lambda c:c['fields'].update(id='trace[0].uuid'),
                       lambda c:c['messages']['role_map'].update(bot='invented_role')]:
            c=copy.deepcopy(config);mutate(c)
            with self.assertRaises(ValidationError):Mapping.model_validate(c)
        record=self.fixture();record['events'][0]['actor']='invalid';self.assertFalse(self.import_record(record).ok)
        record=self.fixture();record['trace']['uuid']=123;self.assertFalse(self.import_record(record).ok)
    def test_json_wrappers_single_objects_bad_lines_duplicate_ids(self):
        config=load_mapping(MAP).model_dump();config['records_path']='export.traces'
        result=self.import_record({'export':{'traces':[self.fixture()]}},Mapping.model_validate(config))
        # Helper wraps records in a list, so use a true wrapper file separately.
        self.assertFalse(result.ok)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'input.json';path.write_text(json.dumps({'export':{'traces':[self.fixture()]}}))
            self.assertTrue(import_file(path,Mapping.model_validate(config)).ok)
            path.write_text(json.dumps(self.fixture()));self.assertTrue(import_file(path,MAP).ok)
            path=path.with_suffix('.jsonl');path.write_text(json.dumps(self.fixture())+'\n\n{broken\n'+json.dumps(self.fixture())+'\n')
            result=import_file(path,MAP);self.assertFalse(result.ok)
            self.assertEqual([d.code for d in result.diagnostics],['invalid_json','duplicate_record_id'])
            self.assertEqual([d.line for d in result.diagnostics],[3,4])
    def test_cli_rejects_entire_batch_without_replacing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'input.json';path.write_text(json.dumps([self.fixture(),{'invalid':'record'}]))
            out=Path(tmp)/'output.jsonl';out.write_text('previous output\n');diag=Path(tmp)/'diagnostics.json'
            with redirect_stdout(io.StringIO()):
                status=main([str(path),'--mapping',str(MAP),'--output',str(out),'--diagnostics',str(diag),'--overwrite'])
            self.assertEqual(status,2);self.assertEqual(out.read_text(),'previous output\n')
            self.assertFalse(json.loads(diag.read_text())['ok'])
            path.write_text(json.dumps([self.fixture()]))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(path),'--mapping',str(MAP),'--output',str(out),'--overwrite']),0)
            trace=Trace.model_validate_json(out.read_text().strip())
            self.assertEqual(trace.id,'vendor-trace-1')
    def test_empty_duplicate_keys_nonfinite_and_unsafe_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.json'
            for text in ['[]', '{"x": 1, "x": 2}', '{"x": NaN}', '{"x": 1e400}']:
                path.write_text(text);self.assertFalse(import_file(path,MAP).ok)
            path=path.with_suffix('.jsonl');path.write_text('\n');self.assertFalse(import_file(path,MAP).ok)
            path=Path(tmp)/'mapping.yaml'
            for text in ['kind: trace\nkind: task\nfields: {}\n', '!!python/object/apply:os.system [echo unsafe]']:
                path.write_text(text)
                with self.assertRaises(Exception):load_mapping(path)

    def test_nan_defaults_and_surrogate_content_rejected_before_output(self):
        config=load_mapping(MAP).model_dump()
        for value in [float('nan'),float('inf'),float('-inf')]:
            c=copy.deepcopy(config);c['defaults']={'metadata':{'score':value}}
            with self.assertRaises(ValidationError):Mapping.model_validate(c)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'input.json';out=Path(tmp)/'out.jsonl';diag=Path(tmp)/'report.json'
            record=self.fixture();record['events'][0]['body']='\ud800'
            path.write_text(json.dumps([record],ensure_ascii=True))
            result=import_file(path,MAP);self.assertFalse(result.ok);self.assertEqual(result.records,[])
            with redirect_stdout(io.StringIO()):
                status=main([str(path),'--mapping',str(MAP),'--output',str(out),'--diagnostics',str(diag)])
            self.assertEqual(status,2);self.assertFalse(out.exists());self.assertFalse(json.loads(diag.read_text())['ok'])
            record['events'][0]['body']='\U0001F642'
            self.assertTrue(self.import_record(record).ok)

    def test_assessment_remains_separate_and_evidence_bound(self):
        trace=import_file(VENDOR,MAP).records[0]
        a=Assessment(id='a1',trace_id=trace.id,verdict='unassessable',assessor_kind='human',assessor_id='annotator-1',rubric_version='v1',evidence_steps=['e0'])
        self.assertIs(a.validate_against(trace),a)
        a.evidence_steps=['absent']
        with self.assertRaises(ValueError):a.validate_against(trace)
        self.assertNotIn('verdict',trace.model_dump())

if __name__=='__main__':unittest.main()
