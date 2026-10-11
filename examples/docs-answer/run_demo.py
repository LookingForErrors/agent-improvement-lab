"""Run a clearly attributed authored-fixture replay, or stop at a real blind queue."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from build_demo import build
from agent_improvement_lab import criteria,graders,workflow,annotation


def run(output, *, human=False):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a fresh output directory; prior labels/results are never overwritten')
    config=build(output/'inputs')
    if human:config=config.with_name('human-config.yaml')
    registry=graders.default_registry()
    graders.load_plugin(registry,Path(__file__).with_name('citation_plugin.py'))
    pack=criteria.load_pack(output/'inputs/criteria.yaml');reports={}
    for arm in ['baseline','candidate']:
        rows=criteria.read(output/f'inputs/{arm}-normalized.json')
        saved=graders.SavedAssessments(criteria.read(output/f'inputs/{arm}-criteria.json'),rows,pack.resolve())
        report=criteria.evaluate(rows,pack,registry,saved);reports[arm]=report
        criteria.write_report(output/f'criteria-{arm}',{'report.json':report.model_dump_json(indent=2)+'\n',
            'metrics.json':json.dumps(criteria.summaries(report),indent=2)+'\n','report.md':criteria.markdown(report)})
    comparison=criteria.compare(reports['baseline'],reports['candidate'])
    (output/'criteria-comparison.json').write_text(json.dumps(comparison,indent=2)+'\n')
    state=workflow.operate(output/'workflow','start',config_path=config)
    if not human:
        state=workflow.operate(output/'workflow','labels-ready')
        state=workflow.operate(output/'workflow','propose',answer={'command':'propose','plan':criteria.read(output/'inputs/plan.json')})
        state=workflow.operate(output/'workflow','approve',answer={'command':'approve','reviewer':'synthetic_test_fixture',
            'action_id':'ground-current-docs','identity':workflow.approval_identity(output/'workflow',state)})
    summary={'mode':'real_human_checkpoint' if human else 'authored_fixture_replay',
        'model_calls':0,'real_human_labels':0,'traces':16,'tasks':4,'families':4,'repeats_per_arm':2,
        'phase':state['phase'],'queue':annotation.queue(output/'workflow/annotations.sqlite3'),
        'warning':'Repairs and semantic decisions were authored in advance; this tests the workflow, not model quality.'}
    (output/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    (output/'RESULT.md').write_text('# Documentation assistant demo\n\nAuthored fixtures · 0 model calls · 0 real human labels\n\n'
        '16 traces, four task families, two repeats per version. The same import, blind annotation, judge-validation, action and comparison interfaces are reused.\n\n'
        'Three scripted defects: invented retention, skipped document lookup, wrong documentation version. The candidate contains authored repairs. This is a workflow demonstration; the displayed difference is not a measured model gain.\n\n'
        'A citation may link to a successfully read page while its claim is false or the version is wrong. The custom deterministic check measures linkage; semantic criteria assess the rest.\n\n'
        + ('The workflow is paused at an empty real human queue. Open annotations.sqlite3 using the annotation server; no fixture decision is inserted into it.\n' if human else
           'The fixture replay reached the comparison checkpoint. The annotation database stays unlabelled; fixture decisions are stored separately and named synthetic_test_fixture.\n'))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--human',action='store_true')
    args=parser.parse_args()
    try:print(json.dumps(run(args.output,human=args.human),indent=2))
    except (ValueError,FileNotFoundError) as error:parser.exit(2,str(error)+'\n')
