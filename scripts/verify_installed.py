"""Verify one public wheel and installed fixture routes outside the checkout."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import sysconfig
import tomllib
import zipfile


def wheel_inventory(snapshot, directory):
    wheels=list(directory.glob('*.whl'))
    if len(wheels)!=1:raise ValueError('Exactly one wheel required')
    wheel=wheels[0];document=tomllib.loads((snapshot/'pyproject.toml').read_text())
    manifest=json.loads((snapshot/'STAGING-MANIFEST.json').read_text())['files']
    packages=document['tool']['setuptools']['packages']
    expected={name for name in manifest if name.split('/')[0] in packages}
    metadata_prefix=document['project']['name'].replace('-','_')+'-'+document['project']['version']+'.dist-info/'
    allowed_metadata={metadata_prefix+n for n in ['METADATA','WHEEL','entry_points.txt','top_level.txt','RECORD']}
    license_members={metadata_prefix+'licenses/'+name:name for name in document['project'].get('license-files',[])}
    if any(name not in manifest for name in license_members.values()):raise ValueError('License missing from staged source')
    allowed_metadata.update(license_members)
    with zipfile.ZipFile(wheel) as bundle:
        members=bundle.namelist();names=set(members)
        if len(members)!=len(names):raise ValueError('Duplicate wheel entry')
        if names!=expected|allowed_metadata:raise ValueError('Wheel inventory differs from public package boundary')
        for info in bundle.infolist():
            if info.filename.startswith('/') or '..' in Path(info.filename).parts or stat.S_ISLNK(info.external_attr>>16):raise ValueError('Unsafe wheel entry')
        for name in expected:
            data=bundle.read(name)
            if hashlib.sha256(data).hexdigest()!=manifest[name]:raise ValueError('Wheel package bytes differ from staged source: '+name)
            if b'/Users/' in data or b'C:\\Users\\' in data:raise ValueError('Machine path in wheel: '+name)
        for member,name in license_members.items():
            if hashlib.sha256(bundle.read(member)).hexdigest()!=manifest[name]:raise ValueError('Wheel license differs from staged source')
    return {'filename':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'files':sorted(names),'package_file_count':len(expected)}


def builder(snapshot, relative, name):
    spec=importlib.util.spec_from_file_location(name,snapshot/relative)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def installed_checks(snapshot, output, mode):
    import agent_improvement_lab,annotation_ui
    from importlib.resources import files
    from agent_improvement_lab import annotation,criteria,graders
    prefix=Path(sys.prefix).resolve();modules=[agent_improvement_lab,annotation_ui,annotation,criteria,graders]
    if mode in ('workflow','combined'):
        from agent_improvement_lab import workflow
        modules.append(workflow)
    if mode in ('execution','combined'):
        from agent_improvement_lab import experiment as ex,experiment_inspect
        modules.extend([ex,experiment_inspect])
    origins={m.__name__:str(Path(m.__file__).resolve()) for m in modules}
    for path in origins.values():
        if not Path(path).is_relative_to(prefix) or Path(path).is_relative_to(snapshot):raise ValueError('Source import instead of installed package: '+path)
    if Path.cwd().is_relative_to(snapshot):raise ValueError('Installed verification must run outside snapshot')
    if os.environ.get('PYTHONPATH') or os.environ.get('PYTHONHOME'):raise ValueError('Unset Python path overrides for installed verification')
    manifest=json.loads((snapshot/'STAGING-MANIFEST.json').read_text())['files']
    installed_files={}
    for package in [agent_improvement_lab,annotation_ui]:
        for name,digest in manifest.items():
            parts=name.split('/')
            if parts[0]!=package.__name__:continue
            data=files(package).joinpath(*parts[1:]).read_bytes()
            actual=hashlib.sha256(data).hexdigest()
            if actual!=digest:raise ValueError('Installed package bytes differ from staged source: '+name)
            installed_files[name]=actual
    checks=[];commands={};scripts=Path(sysconfig.get_path('scripts'))
    for name in ['ail-import','ail-annotate','ail-criteria','ail-judge-metrics','ail-actions','ail-workflow']:
        command=scripts/(name+('.exe' if os.name=='nt' else ''))
        arguments=['experiment','--help'] if name=='ail-workflow' and mode=='execution' else ['--help']
        result=subprocess.run([str(command),*arguments],capture_output=True,text=True,timeout=30)
        if result.returncode:raise ValueError(name+' --help failed')
        commands[name]=True
    checks.extend(['Installed package bytes match staged manifest','Six installed commands outside source'])
    resources={}
    for name in ['index.html','app.js','style.css']:
        data=files(annotation_ui).joinpath(name).read_bytes()
        if data!=(snapshot/'annotation_ui'/name).read_bytes():raise ValueError('Installed resource differs: '+name)
        resources[name]=hashlib.sha256(data).hexdigest()
    checks.append('Installed UI resources match wheel source')
    if mode in ('workflow','combined'):
        b=builder(snapshot,'examples/docs-answer/build_demo.py','installed_docs_fixture')
        config=b.build(output/'workflow-inputs')
        def checkpoint(path):
            state=workflow.operate(path,'start',config_path=config)
            assert state['phase']=='annotation' and annotation.queue(path/'annotations.sqlite3')['reviewed']==0
            workflow.operate(path,'labels-ready')
            return workflow.operate(path,'propose',answer={'command':'propose','plan':criteria.read(config.parent/'plan.json')})
        def answer(path,state,command):return {'command':command,'reviewer':'synthetic_test_fixture','action_id':'ground-current-docs','identity':workflow.approval_identity(path,state)}
        path=output/'workflow';state=checkpoint(path);state=workflow.operate(path,'approve',answer=answer(path,state,'approve'))
        assert state['phase']=='complete' and annotation.export_private(path/'annotations.sqlite3')==[]
        checks.append('Installed scripted graph replay and empty real annotation DB')
        path=output/'rejected';state=checkpoint(path);assert workflow.operate(path,'reject',answer=answer(path,state,'reject'))['phase']=='reject'
        assert not (path/'comparison.json').exists();checks.append('Installed rejection preserves stopped decision')
        path=output/'resumed';state=checkpoint(path)
        try:workflow.operate(path,'approve',answer=answer(path,state,'approve'),crash_after_comparison=True)
        except RuntimeError as error:assert 'simulated crash' in str(error)
        else:raise AssertionError('Expected injected report crash')
        before=(path/'comparison.json').read_bytes();assert workflow.operate(path,'resume')['phase']=='complete'
        assert (path/'comparison.json').read_bytes()==before;checks.append('Installed graph resume preserves comparison')
        path=output/'empty-human';assert workflow.operate(path,'start',config_path=config.parent/'human-config.yaml')['phase']=='annotation'
        try:workflow.operate(path,'labels-ready')
        except ValueError as error:assert 'all selected' in str(error)
        else:raise AssertionError('Empty human checkpoint incorrectly advanced')
        checks.append('Installed empty human checkpoint cannot advance')
    if mode in ('execution','combined'):
        if os.name!='posix':raise ValueError('Execution supervision currently requires POSIX')
        for kind,relative in [('native','build_demo.py'),('recovery','build_recovery_demo.py')]:
            b=builder(snapshot,'examples/routing-experiment/'+relative,'installed_'+kind+'_fixture')
            config=b.build(output/(kind+'-inputs'));workspace=output/(kind+'-run')
            state=ex.prepare(config,workspace);ex.approve(workspace,state['manifest_sha256'],'installed_scripted_fixture_not_human')
            state=ex.run(workspace)
            assert state['phase']=='complete' and state['completed_slots']==8 and state['http_starts']==0
            assert not (workspace/'requests.jsonl').exists()
            before=ex.sha(workspace/'registry.json');ex.finalize(workspace);assert ex.sha(workspace/'registry.json')==before
            if kind=='recovery':assert (workspace/'TRACES.md').read_text().count('**Scaffold control**')==4
            # The archived engine must be the installed bytes, not the source builder.
            for name in ex.SOURCES:assert ex.sha(workspace/'source'/name)==ex.sha(Path(ex.__file__).parent/name)
            checks.append('Installed '+kind+' native fixture, zero HTTP and report-only idempotence')
    # Fixture builders must not switch subsequent imports back to the checkout.
    for name,module in list(sys.modules.items()):
        if name.split('.')[0] not in ['agent_improvement_lab','annotation_ui'] or not getattr(module,'__file__',None):continue
        path=Path(module.__file__).resolve()
        if not path.is_relative_to(prefix) or path.is_relative_to(snapshot):raise ValueError('Fixture switched to source import: '+name)
        origins[name]=str(path)
    return {'status':'pass','mode':mode,'module_origins':origins,'installed_files':installed_files,'commands':commands,'resources':resources,'checks':checks,'model_calls':0,'real_human_labels':0,'fixture_source_distinct_from_installed_engine':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--wheel-directory',type=Path);parser.add_argument('--inventory-only',action='store_true')
    parser.add_argument('--mode',choices=['workflow','execution','combined'],default='combined')
    args=parser.parse_args();snapshot=args.snapshot.resolve();output=args.output.resolve()
    if output.exists():parser.error('Choose a fresh output directory')
    output.mkdir(parents=True)
    result={'status':'started'}
    try:
        wheel=wheel_inventory(snapshot,args.wheel_directory.resolve()) if args.wheel_directory else None
        if args.inventory_only and wheel is None:raise ValueError('--inventory-only requires --wheel-directory')
        result={'status':'pass','wheel':wheel,'model_calls':0} if args.inventory_only else dict(installed_checks(snapshot,output,args.mode),wheel=wheel)
    except Exception as error:
        result={'status':'failed','error':type(error).__name__+': '+str(error),'model_calls':0}
    (output/'installed-check.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
    return 0 if result['status']=='pass' else 1

if __name__=='__main__':raise SystemExit(main())
