"""Create a fresh allowlisted install-check snapshot; never publish or delete data."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CORE=['__init__.py','schemas.py','import_traces.py','annotation.py','criteria.py','graders.py','judge_metrics.py','failure_actions.py','workflow.py',
      'workflow_cli.py','experiment.py','experiment_inspect.py','evidence_scaffold.py','actor_state.py','action_gate.py',
      'routing_task.py','routing_criteria.py','routing_measurement.py','local_http.py','polza_http.py']
TESTS=['test_import.py','test_annotation.py','test_criteria.py','test_judge_metrics.py','test_failure_actions.py','test_workflow.py','test_docs_answer.py','test_packaging.py','test_experiment.py','test_recovery_guard.py','test_evidence_scaffold.py','test_routing_measurement.py','test_staging.py']
DOCS=['package-checks.md','quickstart.md','docs-answer-demo.md','trace-import.md','annotation.md','criteria.md','judge-metrics.md','failure-actions.md','workflow.md','evaluation-axes.md','experiment.md']


def inventory():
    paths=['LICENSE','pyproject.toml','MANIFEST.in','requirements/workflow.lock.txt','requirements/execution.lock.txt','requirements/combined.lock.txt','requirements/build.lock.txt','scripts/stage_offline.py','scripts/verify_installed.py','.github/workflows/offline.yml']
    paths += ['agent_improvement_lab/'+p for p in CORE]
    paths += ['annotation_ui/'+p for p in ['__init__.py','index.html','app.js','style.css']]
    paths += ['tests/'+p for p in TESTS]+['docs/'+p for p in DOCS]
    paths += ['mappings/vendor-example.yaml','mappings/docs-answer.yaml','configs/docs-answer.en.yaml']
    paths += ['examples/docs-answer/'+p for p in ['build_demo.py','citation_plugin.py','run_demo.py']]
    paths += ['examples/routing-loop/build_demo.py','examples/criteria-demo/build_demo.py',
              'tests/fixtures/import/vendor.json','tests/fixtures/import/vendor.jsonl']
    paths += ['examples/routing-experiment/'+p for p in ['build_demo.py','build_recovery_demo.py','cases.json','baseline.txt','candidate.txt','common.txt','criteria.yml']]
    return sorted(set(paths))


def stage(output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a fresh snapshot directory')
    names=inventory()
    root=ROOT.resolve()
    for name in names:
        path=ROOT/name
        ancestors=[path,*path.parents[:len(Path(name).parts)-1]]
        if not path.is_file() or any(parent.is_symlink() for parent in ancestors) or not path.resolve().is_relative_to(root):raise ValueError('Required regular file missing or unsafe: '+name)
    output.mkdir(parents=True)
    files={}
    for name in names:
        source=ROOT/name;target=output/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target);files[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (output/'README.md').write_text('# Agent Improvement Lab\n\n'
        'A research prototype with synthetic improvement-loop examples.\n\n'
        'Run synthetic examples offline or use a caller-managed model endpoint.\n\n'
        'See [quickstart](docs/quickstart.md) and the [documentation assistant example](docs/docs-answer-demo.md).\n\n'
        'Licensed under [MIT](LICENSE).\n')
    files['README.md']=hashlib.sha256((output/'README.md').read_bytes()).hexdigest()
    (output/'STAGING-MANIFEST.json').write_text(json.dumps({'kind':'unpublished_install_check_snapshot','files':files,
        'excluded':['artifacts','annotation_data','datasets','tasks','logs','decisions','venvs','live_experiment','live_runner','reviewed_experiment','structured_routing'],
        'license':'MIT','publication':False},indent=2)+'\n')
    return files


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    try:print(json.dumps({'files':len(stage(args.output)),'output':str(Path(args.output).resolve())}))
    except ValueError as error:parser.exit(2,str(error)+'\n')
