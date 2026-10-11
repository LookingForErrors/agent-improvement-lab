"""Finite YAML dot-path import. No model, network, dynamic code or agent scoring."""
import argparse
import json
import math
import os
import re
import tempfile
from dataclasses import asdict,dataclass,field
from pathlib import Path
from typing import Literal
import yaml
from pydantic import Field,JsonValue,ValidationError,model_validator
from .schemas import Model,Source,Task,Trace

MISSING=object()
PATH=re.compile(r'^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$')
TASK_FIELDS={'id','objective','family_id','split','language','context','metadata'}
TRACE_FIELDS={'id','task_id','family_id','split','language','provenance','agent_version','metadata'}
MESSAGE_FIELDS={'step_id','role','content','reasoning','tool_call_id','function'}
ROLES={'system','developer','user','assistant','tool'}

class ToolMapping(Model):
    path:str='tool_calls'
    fields:dict[str,str]
    @model_validator(mode='after')
    def validate_fields(self):
        if set(self.fields)!={'id','name','arguments'}:raise ValueError('tool fields must be id, name, arguments')
        check_paths([self.path,*self.fields.values()]);return self

class MessageMapping(Model):
    path:str
    fields:dict[str,str]
    role_map:dict[str,str]=Field(default_factory=dict)
    tool_calls:ToolMapping | None=None
    @model_validator(mode='after')
    def validate_fields(self):
        if 'role' not in self.fields or not set(self.fields)<=MESSAGE_FIELDS:
            raise ValueError('message fields require role and may contain only documented fields')
        if not set(self.role_map.values())<=ROLES:raise ValueError('role_map targets must be canonical roles')
        check_paths([self.path,*self.fields.values()]);return self

class TaskMapping(Model):
    fields:dict[str,str]
    defaults:dict[str,JsonValue]=Field(default_factory=dict)
    @model_validator(mode='after')
    def validate_fields(self):
        check_fields(self.fields,self.defaults,TASK_FIELDS);return self

class Mapping(Model):
    mapping_version:Literal['1']='1'
    kind:Literal['trace','task']='trace'
    records_path:str | None=None
    fields:dict[str,str]
    defaults:dict[str,JsonValue]=Field(default_factory=dict)
    messages:MessageMapping | None=None
    task:TaskMapping | None=None
    @model_validator(mode='after')
    def validate_fields(self):
        check_fields(self.fields,self.defaults,TRACE_FIELDS if self.kind=='trace' else TASK_FIELDS)
        if self.records_path is not None:check_paths([self.records_path])
        if self.kind=='trace' and self.messages is None:raise ValueError('trace mapping requires messages')
        if self.kind=='task' and (self.messages is not None or self.task is not None):
            raise ValueError('task mapping cannot contain messages or an embedded task mapping')
        return self

def check_paths(paths):
    if any(not PATH.fullmatch(p) for p in paths):raise ValueError('mapping supports object dot paths only')

def check_fields(fields,defaults,allowed):
    if not set(fields)<=allowed or not set(defaults)<=allowed:raise ValueError('unknown mapped/default field')
    check_paths(fields.values())

class ImportProblem(ValueError):
    def __init__(self,code,path,message):
        self.code=code;self.path=path;super().__init__(message)

@dataclass
class Diagnostic:
    severity:str
    code:str
    record_index:int | None
    line:int | None
    path:str
    message:str

@dataclass
class ImportResult:
    records:list[Task|Trace]=field(default_factory=list)
    diagnostics:list[Diagnostic]=field(default_factory=list)
    @property
    def ok(self):return not any(d.severity=='error' for d in self.diagnostics)

class UniqueSafeLoader(yaml.SafeLoader):pass

def yaml_mapping(loader,node,deep=False):
    loader.flatten_mapping(node);result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if not isinstance(key,str) or key in result:raise ValueError('YAML keys must be unique strings')
        result[key]=loader.construct_object(value_node,deep=deep)
    return result
UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,yaml_mapping)

def load_mapping(path):
    return Mapping.model_validate(yaml.load(Path(path).read_text(encoding='utf-8'),Loader=UniqueSafeLoader))

def strict_json(text):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate JSON object key')
            result[key]=value
        return result
    def constant(value):raise ValueError('non-finite JSON number is unsupported')
    def finite_float(value):
        number=float(value)
        if not math.isfinite(number):raise ValueError('non-finite JSON number is unsupported')
        return number
    return json.loads(text,object_pairs_hook=unique,parse_constant=constant,parse_float=finite_float)

def get(record,path):
    value=record
    for key in path.split('.'):
        if not isinstance(value,dict) or key not in value:return MISSING
        value=value[key]
    return value

def mapped(record,fields,defaults=None):
    result=dict(defaults or {})
    for target,path in fields.items():
        value=get(record,path)
        if value is not MISSING:result[target]=value
    return result

def normalize(record,mapping,source):
    if not isinstance(record,dict):raise ImportProblem('record_type','$','each record must be a JSON object')
    data=mapped(record,mapping.fields,mapping.defaults);data['source']=source
    if mapping.kind=='task':return Task.model_validate(data)
    spec=mapping.messages;events=get(record,spec.path)
    if events is MISSING:raise ImportProblem('missing_field',spec.path,'message list is missing')
    if not isinstance(events,list):raise ImportProblem('field_type',spec.path,'messages must be a list')
    messages=[]
    for i,event in enumerate(events):
        base=f'{spec.path}[{i}]'
        if not isinstance(event,dict):raise ImportProblem('message_type',base,'message must be an object')
        message=mapped(event,spec.fields)
        if 'step_id' not in message:message['step_id']=f's{i:04d}'
        if 'role' not in message:raise ImportProblem('missing_field',f'{base}.{spec.fields["role"]}','message role is missing')
        role=message['role']
        if isinstance(role,str):message['role']=spec.role_map.get(role,role)
        if spec.tool_calls is not None:
            tools=get(event,spec.tool_calls.path)
            if tools is not MISSING:
                if not isinstance(tools,list):raise ImportProblem('field_type',f'{base}.{spec.tool_calls.path}','tool_calls must be a list')
                calls=[]
                for j,tool in enumerate(tools):
                    location=f'{base}.{spec.tool_calls.path}[{j}]'
                    if not isinstance(tool,dict):raise ImportProblem('tool_type',location,'tool call must be an object')
                    call=mapped(tool,spec.tool_calls.fields)
                    if isinstance(call.get('arguments'),str):
                        try:call['arguments']=strict_json(call['arguments'])
                        except ValueError:raise ImportProblem('arguments_json',f'{location}.arguments','tool arguments string is not valid JSON')
                    calls.append(call)
                message['tool_calls']=calls
        if 'content' not in message and not (message['role']=='assistant' and message.get('tool_calls')):
            path=spec.fields.get('content','content')
            raise ImportProblem('missing_field',f'{base}.{path}','message content is missing')
        messages.append(message)
    data['messages']=messages
    if mapping.task is not None:data['task']=mapped(record,mapping.task.fields,mapping.task.defaults)
    return Trace.model_validate(data)

def validation_diagnostics(error,index,line):
    return [Diagnostic('error','schema_validation',index,line,'.'.join(str(p) for p in item['loc']) or '$',item['msg'])
            for item in error.errors(include_input=False,include_url=False)]

def import_file(path,mapping):
    """Collect normalized records and structural diagnostics. Never create assessments."""
    path=Path(path);mapping=load_mapping(mapping) if not isinstance(mapping,Mapping) else mapping
    result=ImportResult();seen=set()
    def consume(record,index,line):
        try:
            source=Source(file=path.name,record_index=index,line=line,record=record) if isinstance(record,dict) else None
            obj=normalize(record,mapping,source)
            if obj.id in seen:raise ImportProblem('duplicate_record_id','id','record id is duplicated in this input')
            seen.add(obj.id);result.records.append(obj)
            if isinstance(obj,Trace):
                calls={c.id for m in obj.messages for c in m.tool_calls};answered={m.tool_call_id for m in obj.messages if m.role=='tool'}
                if calls-answered:result.diagnostics.append(Diagnostic('warning','unanswered_tool_call',index,line,'messages','one or more tool calls have no recorded result'))
        except ImportProblem as error:result.diagnostics.append(Diagnostic('error',error.code,index,line,error.path,str(error)))
        except ValidationError as error:result.diagnostics.extend(validation_diagnostics(error,index,line))
    if path.suffix.lower()=='.jsonl':
        if mapping.records_path is not None:raise ValueError('records_path is for JSON wrappers; JSONL requires one record per line')
        with path.open(encoding='utf-8') as stream:
            index=0
            for line_no,text in enumerate(stream,1):
                if not text.strip():continue
                index+=1
                try:record=strict_json(text)
                except ValueError:
                    result.diagnostics.append(Diagnostic('error','invalid_json',index,line_no,'$','line is not valid unique-key JSON'));continue
                consume(record,index,line_no)
    elif path.suffix.lower()=='.json':
        try:document=strict_json(path.read_text(encoding='utf-8'))
        except ValueError:
            result.diagnostics.append(Diagnostic('error','invalid_json',None,None,'$','file is not valid unique-key JSON'));return result
        if mapping.records_path is not None:
            document=get(document,mapping.records_path)
            if document is MISSING or not isinstance(document,list):
                result.diagnostics.append(Diagnostic('error','records_path',None,None,mapping.records_path,'wrapper path must identify a list'));return result
        records=document if isinstance(document,list) else [document]
        for index,record in enumerate(records,1):consume(record,index,None)
    else:raise ValueError('input extension must be .json or .jsonl')
    if not result.records and not result.diagnostics:
        result.diagnostics.append(Diagnostic('error','empty_input',None,None,'$','input contains no records'))
    return result

def atomic_write(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
            temporary=f.name;f.write(text)
        os.replace(temporary,path)
    finally:
        if temporary is not None and os.path.exists(temporary):os.unlink(temporary)

def main(argv=None):
    parser=argparse.ArgumentParser(description='Normalize sequential traces/tasks; never evaluate agent quality.')
    parser.add_argument('input',type=Path);parser.add_argument('--mapping',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--diagnostics',type=Path)
    parser.add_argument('--overwrite',action='store_true');args=parser.parse_args(argv)
    destinations=[args.output]+([args.diagnostics] if args.diagnostics else [])
    if len({p.resolve() for p in destinations})!=len(destinations):parser.error('output and diagnostics must differ')
    for dest in destinations:
        if dest.resolve() in {args.input.resolve(),args.mapping.resolve()}:parser.error('output cannot replace input or mapping')
        if dest.exists() and not args.overwrite:parser.error('destination exists; choose another path or use --overwrite')
    try:result=import_file(args.input,args.mapping)
    except (OSError,ValueError,yaml.YAMLError) as error:
        print(json.dumps({'ok':False,'error':'import_configuration_or_io','message':str(error)},ensure_ascii=False));return 2
    report={'ok':result.ok,'accepted_records':len(result.records),'diagnostics':[asdict(d) for d in result.diagnostics]}
    output_text=''.join(r.model_dump_json()+'\n' for r in result.records) if result.ok else None
    if result.ok:atomic_write(args.output,output_text)
    if args.diagnostics:atomic_write(args.diagnostics,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False));return 0 if result.ok else 2

if __name__=='__main__':raise SystemExit(main())
