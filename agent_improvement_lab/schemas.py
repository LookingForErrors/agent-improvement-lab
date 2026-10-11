"""Sequential trace contracts. Import validity is separate from agent quality."""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

def ensure_json_safe(value):
    if isinstance(value,float) and not math.isfinite(value):
        raise ValueError('non-finite numbers cannot be preserved as JSON')
    if isinstance(value,str) and any(0xD800<=ord(c)<=0xDFFF for c in value):
        raise ValueError('unpaired surrogate code points cannot be serialized as Unicode JSON')
    if isinstance(value,dict):
        for key,item in value.items():ensure_json_safe(key);ensure_json_safe(item)
    elif isinstance(value,(list,tuple)):
        for item in value:ensure_json_safe(item)

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    @model_validator(mode='before')
    @classmethod
    def json_safe(cls,data):
        ensure_json_safe(data);return data

class Source(Model):
    file: str
    record_index: int=Field(ge=1)
    line: int | None=None
    record: dict[str,JsonValue]

class Task(Model):
    schema_version: Literal['1']='1'
    id: str=Field(min_length=1)
    objective: str=Field(min_length=1)
    family_id: str | None=None
    split: Literal['working','heldout'] | None=None
    language: str | None=None
    context: JsonValue=None
    metadata: dict[str,JsonValue]=Field(default_factory=dict)
    source: Source | None=None

class ToolCall(Model):
    id: str=Field(min_length=1)
    name: str=Field(min_length=1)
    arguments: dict[str,JsonValue]

class Message(Model):
    step_id: str=Field(min_length=1)
    role: Literal['system','developer','user','assistant','tool']
    content: JsonValue=''
    reasoning: JsonValue=None
    tool_calls: list[ToolCall]=Field(default_factory=list)
    tool_call_id: str | None=None
    function: str | None=None

    @model_validator(mode='after')
    def shape(self):
        if self.tool_calls and self.role!='assistant':raise ValueError('tool_calls require assistant role')
        if len(self.tool_calls)>1:raise ValueError('multiple calls in one message are outside the sequential MVP')
        if self.role=='tool' and not self.tool_call_id:raise ValueError('tool message requires tool_call_id')
        if self.role!='tool' and self.tool_call_id is not None:raise ValueError('tool_call_id requires tool role')
        if self.function is not None and self.role!='tool':raise ValueError('result function requires tool role')
        return self

class Trace(Model):
    schema_version: Literal['1']='1'
    id: str=Field(min_length=1)
    task_id: str | None=None
    task: Task | None=None
    family_id: str | None=None
    split: Literal['working','heldout'] | None=None
    language: str | None=None
    provenance: str | None=None
    agent_version: str | None=None
    messages: list[Message]=Field(min_length=1)
    metadata: dict[str,JsonValue]=Field(default_factory=dict)
    source: Source | None=None

    @model_validator(mode='after')
    def links(self):
        if self.task is not None and self.task_id is not None and self.task.id!=self.task_id:
            raise ValueError('embedded task id differs from task_id')
        steps=set();calls={};results=set()
        for message in self.messages:
            if message.step_id in steps:raise ValueError('duplicate step_id')
            steps.add(message.step_id)
            for call in message.tool_calls:
                if call.id in calls:raise ValueError('duplicate tool call id')
                calls[call.id]=call
            if message.role=='tool':
                cid=message.tool_call_id
                if cid not in calls:raise ValueError('tool result refers to no earlier call')
                if cid in results:raise ValueError('duplicate result for tool call')
                if message.function is not None and message.function!=calls[cid].name:
                    raise ValueError('tool result function differs from call name')
                results.add(cid)
        # An unanswered call can be an observed truncated/failed execution, not an import error.
        return self

class Assessment(Model):
    schema_version: Literal['1']='1'
    id: str=Field(min_length=1)
    trace_id: str=Field(min_length=1)
    verdict: Literal['correct','incorrect','unassessable']
    assessor_kind: Literal['human','llm','deterministic','generator']
    assessor_id: str=Field(min_length=1)
    rubric_version: str=Field(min_length=1)
    evidence_steps: list[str]=Field(default_factory=list)
    error_types: list[str]=Field(default_factory=list)
    explanation: str=''
    metadata: dict[str,JsonValue]=Field(default_factory=dict)

    def validate_against(self,trace:Trace):
        if self.trace_id!=trace.id:raise ValueError('assessment trace_id mismatch')
        if not set(self.evidence_steps)<={m.step_id for m in trace.messages}:
            raise ValueError('assessment evidence references missing steps')
        return self
