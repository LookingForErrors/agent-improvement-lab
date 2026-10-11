"""Inspect provider adapter for a fixed localhost Chat Completions endpoint.

Uses the standard library because ENV-001 did not install the optional OpenAI SDK.
No remote hosts, redirects, credentials, model loading or request retries.
"""
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
import urllib.request
import urllib.error
from inspect_ai.model import ModelAPI,ModelCall,ModelOutput,ModelUsage,ChatCompletionChoice,ChatMessageAssistant,modelapi
from inspect_ai.tool import ToolCall
from .import_traces import strict_json

ENDPOINT='http://127.0.0.1:1234/v1/chat/completions'
BUDGET_PATH=None
EXPERIMENT_ID=None

def configure_budget(path,experiment):
    global BUDGET_PATH,EXPERIMENT_ID
    BUDGET_PATH=Path(path);EXPERIMENT_ID=experiment

def reserve_request(body):
    if BUDGET_PATH is None:return
    count=sum(1 for line in BUDGET_PATH.read_text().splitlines() if line.strip()) if BUDGET_PATH.exists() else 0
    if count>=240:raise RuntimeError('authorized HTTP request budget exhausted')
    with BUDGET_PATH.open('a') as f:
        f.write(json.dumps({'experiment':EXPERIMENT_ID,'time':time.time(),'model':body['model'],'seed':body.get('seed'),'request_started':True})+'\n')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def request_body(model,input,tools,tool_choice,config):
    messages=[]
    for message in input:
        item={'role':message.role,'content':message.text}
        if getattr(message,'tool_call_id',None):item['tool_call_id']=message.tool_call_id
        if getattr(message,'error',None):item['content']=json.dumps({'status':'ERROR','code':'TOOL_EXECUTION_ERROR','error':asdict(message.error)})
        if getattr(message,'tool_calls',None):item['tool_calls']=[{'id':c.id,'type':'function','function':{'name':c.function,'arguments':json.dumps(c.arguments)}} for c in message.tool_calls]
        messages.append(item)
    body={'model':model,'messages':messages,'max_tokens':config.max_tokens or 256,'temperature':config.temperature if config.temperature is not None else 0.4,'stream':False}
    if config.seed is not None:body['seed']=config.seed
    if tools:
        body['tools']=[{'type':'function','function':{'name':t.name,'description':t.description,'parameters':t.parameters.model_dump(mode='json',exclude_none=True)}} for t in tools]
        body['tool_choice']='required' if tool_choice=='any' else tool_choice if isinstance(tool_choice,str) else {'type':'function','function':{'name':tool_choice.name}}
        body['parallel_tool_calls']=False
    return body

def parse_response(response,model):
    choice=response['choices'][0];message=choice['message'];calls=[]
    for call in message.get('tool_calls') or []:
        function=call['function'];arguments=function.get('arguments',{});error=None
        try:
            if isinstance(arguments,str):arguments=strict_json(arguments)
            if not isinstance(arguments,dict):raise ValueError('tool arguments must be an object')
        except ValueError:
            error='model emitted invalid JSON object arguments';arguments={}
        calls.append(ToolCall(id=call['id'],function=function['name'],arguments=arguments,parse_error=error))
    usage=response.get('usage');model_usage=None
    if usage is not None:
        model_usage=ModelUsage(input_tokens=usage['prompt_tokens'],output_tokens=usage['completion_tokens'],total_tokens=usage['total_tokens'])
    stop=choice.get('finish_reason','unknown')
    if stop not in {'stop','length','tool_calls','content_filter','model_length','unknown'}:stop='unknown'
    return ModelOutput(model=model,choices=[ChatCompletionChoice(message=ChatMessageAssistant(content=message.get('content') or '',tool_calls=calls or None),stop_reason=stop)],usage=model_usage,
        metadata={'provider_response':response,'usage_available':usage is not None})

@modelapi(name='localhttp')
class LocalHTTP(ModelAPI):
    def __init__(self,model_name,**kwargs):
        if model_name!='ail-routing-live':raise ValueError('only the explicitly owned model identifier is allowed')
        super().__init__(model_name=model_name,base_url='http://127.0.0.1:1234/v1',config=kwargs.get('config'))
    async def generate(self,input,tools,tool_choice,config):
        body=request_body(self.model_name,input,tools,tool_choice,config);started=time.time()
        def send():
            request=urllib.request.Request(ENDPOINT,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
            # Ignore proxy environment: localhost only, no redirect following.
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            try:
                with opener.open(request,timeout=min(config.timeout or 90,90)) as response:
                    return strict_json(response.read().decode('utf-8')),None
            except urllib.error.HTTPError as error:
                # Keep provider diagnostics in the raw Inspect call, not agent labels.
                payload={'http_status':error.code,'body':error.read().decode('utf-8',errors='replace')}
                return payload,RuntimeError(f"local provider HTTP {error.code}: {payload['body']}")
        reserve_request(body)
        response,error=await asyncio.to_thread(send)
        return error or parse_response(response,self.model_name),ModelCall(request=body,response=response,time=time.time()-started)
