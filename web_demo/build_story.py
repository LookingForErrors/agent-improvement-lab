"""Bind a reviewed public family experiment to a static, recorded-data story."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

PUBLIC_NAMES = {
    'experiment-fixture.json': 'family-experiment.json',
    'example-selection.json': 'family-examples.json',
    'stability-diagnostics.json': 'family-stability.json',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def validate(data, examples):
    public_text=json.dumps({'data':data,'examples':examples})
    if any(token in public_text for token in ('/Users/', '/private/', '.env.local', 'Bearer ', 'api_key', 'Authorization')):
        raise ValueError('Private field in public experiment')
    if data.get('format') != 'saved-family-experiment-web-v2':
        raise ValueError('Unsupported saved experiment')
    traces = {t['id']: t for t in data['traces']}
    cells = {(c['trace_id'], c['criterion_id']): c for c in data['results']}
    if len(traces) != len(data['traces']) or len(cells) != len(data['results']):
        raise ValueError('Duplicate identities')
    primary = data['action']['verification']['primary_measure']
    paired = {p['task_id']: p for p in data['pairs']}
    if len(paired) != len(data['pairs']):
        raise ValueError('Duplicate paired task')
    for pair in data['pairs']:
        for arm in ('baseline', 'candidate'):
            t = traces[pair[arm]]
            if t['arm'] != arm or t['task_id'] != pair['task_id'] or t['family_id'] != pair['family_id']:
                raise ValueError('Broken pairing')
            if (t['id'], primary) not in cells:
                raise ValueError('Primary result missing')
    for cell in data['results']:
        t = traces[cell['trace_id']]
        if cell['trace_sha256'] != t['source_sha256'] or not set(cell['evidence_steps']) <= {m['step_id'] for m in t['messages']}:
            raise ValueError('Unbound result evidence')
    for e in examples['examples']:
        pair = paired[e['task_id']]
        for arm in ('baseline', 'candidate'):
            selected = e[arm]
            c = cells[(selected['trace_id'], primary)]
            if selected['trace_id'] != pair[arm] or selected['trace_sha256'] != c['trace_sha256'] or selected['primary_verdict'] != c['verdict'] or selected['evidence_steps'] != c['evidence_steps']:
                raise ValueError('Illustration is not source-bound')
    prior = {t['id']: t for t in data['prior_traces']}
    for e in data['action_evidence']:
        t = prior[e['trace_id']]
        if e['split'] != 'working' or t['split'] != 'working' or e['trace_sha256'] != t['source_sha256'] or not set(e['evidence_steps']) <= {m['step_id'] for m in t['messages']}:
            raise ValueError('Intervention evidence must precede heldout')
    if data['provenance']['new_model_calls'] != 0 or data['decision']['human_gold'] or data['decision']['default_adopted']:
        raise ValueError('Unsupported truth promotion')
    return primary


def export(source, output):
    source, output = Path(source), Path(output)
    manifest = read(source / 'TRANSFER-MANIFEST.json')
    if any(digest(source / name) != sha for name, sha in manifest['files'].items()):
        raise ValueError('Reviewed transfer has changed')
    data, examples = read(source / 'experiment-fixture.json'), read(source / 'example-selection.json')
    if examples['experiment_fixture_sha256'] != digest(source / 'experiment-fixture.json'):
        raise ValueError('Illustrations belong to another experiment')
    primary = validate(data, examples)
    selected = {e[a]['trace_id'] for e in examples['examples'] for a in ('baseline', 'candidate')}
    story = {k: data[k] for k in ('id', 'title', 'provenance', 'pairs', 'criteria', 'action', 'action_evidence', 'prompt', 'prior_traces', 'statistics', 'decision', 'capture', 'costs')}
    story.update(format='agent-improvement-story-v1', source_fixture_sha256=digest(source / 'experiment-fixture.json'),
                 primary_criterion=primary, traces=[t for t in data['traces'] if t['id'] in selected],
                 results=[c for c in data['results'] if c['criterion_id'] == primary or c['trace_id'] in selected],
                 examples=examples['examples'])
    text = json.dumps(story, ensure_ascii=False, separators=(',', ':')) + '\n'
    if any(token in text for token in ('/Users/', '/private/', '.env.local', 'Bearer ', 'api_key')):
        raise ValueError('Private field in story')
    output.mkdir(parents=True, exist_ok=True)
    for original, target in PUBLIC_NAMES.items():
        shutil.copyfile(source / original, output / target)
    (output / 'story-data.json').write_text(text)
    binding = {'format': 'recorded-improvement-site-binding-v1', 'source_files': manifest['files'],
               'files': {name: digest(output / name) for name in [*PUBLIC_NAMES.values(), 'story-data.json']},
               'new_model_calls': 0, 'human_gold': False, 'publication': False,
               'primary_traces': len(data['traces']), 'pairs': len(data['pairs']),
               'families': len({p['family_id'] for p in data['pairs']}),
               'primary_counts': {a: dict(Counter(c['verdict'] for c in data['results'] if c['criterion_id'] == primary and c['trace_id'] in {p[a] for p in data['pairs']})) for a in ('baseline', 'candidate')}}
    (output / 'family-transfer.json').write_text(json.dumps(binding, indent=2) + '\n')
    return binding


def validate_projection(data, story, examples):
    primary = validate(data, examples)
    for key in ('id', 'title', 'provenance', 'pairs', 'criteria', 'action', 'action_evidence', 'prompt', 'prior_traces', 'statistics', 'decision', 'capture', 'costs'):
        if story[key] != data[key]:
            raise ValueError('Story changed source field: ' + key)
    selected = {e[arm]['trace_id'] for e in examples['examples'] for arm in ('baseline', 'candidate')}
    if story['traces'] != [t for t in data['traces'] if t['id'] in selected] or story['results'] != [c for c in data['results'] if c['criterion_id'] == primary or c['trace_id'] in selected] or story['examples'] != examples['examples']:
        raise ValueError('Story projection differs from saved sources')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.output)))
