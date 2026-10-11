"""Prepare an unpublished exact source/demo snapshot; no Git or hosting writes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_offline_boundary',ROOT/'scripts/stage_offline.py')
offline=importlib.util.module_from_spec(spec);spec.loader.exec_module(offline)
EXTRA=['scripts/stage_release.py','tests/test_public_demo.py','tests/test_catalog.py','web_demo/index.html','web_demo/app.js','web_demo/style.css','web_demo/build_demo.py','web_demo/README.md','web_demo/catalog.html','web_demo/catalog.js','web_demo/build_catalog.py','web_demo/catalog-selection.json','web_demo/catalog-fixture.json','docs/public-demo.md','docs/provenance.md']
BASE_DEMO=['index.html','app.js','style.css','demo-data.json','demo-data.js','DEMO-MANIFEST.json']
CATALOG_DEMO=BASE_DEMO+['catalog.html','catalog.js','catalog-data.json','catalog-data.js']
LEGACY_DEMO=CATALOG_DEMO+['experiment.html','experiment.js','experiment-data.json','experiment-data.js']
FAMILY_ASSETS=['guided.html','pilot.html','showcase.css','family.js','story.js','results.js','family-experiment.json','family-examples.json','family-stability.json','family-transfer.json','story-data.json']
FAMILY_DEMO=LEGACY_DEMO+FAMILY_ASSETS
REPLAY_ASSETS=['curated-experiment.json','curated-story.json','curated-transfer.json']
DEMO=FAMILY_DEMO+REPLAY_ASSETS
EXTRA+=['web_demo/experiment.html','web_demo/experiment.js','web_demo/build_experiment.py','web_demo/experiment-fixture.json','tests/test_saved_experiment.py']
EXTRA+=['web_demo/'+name for name in FAMILY_ASSETS]+['web_demo/build_story.py','tests/test_family_site.py']
EXTRA+=['web_demo/'+name for name in REPLAY_ASSETS]+['web_demo/build_replay.py','web_demo/replay-plan.json','tests/test_curated_replay.py']
README='''# Agent Improvement Lab

Turn agent traces into evidence, concrete product changes and reproducible comparisons

JSON/JSONL → versioned criteria → annotation / judge validation → failure report →
product Action → approved experiment → paired comparison → next test inputs

## Try the example

Open `demo/index.html` for the bundled static browser example. Run the baseline,
inspect evidence, replay or reject the supplied candidate, then generate test inputs.
No installation, provider or network is needed for the built browser assets

Open `demo/catalog.html` to browse 30 curated working scenarios and 180 scripted
routing traces, with filters and full tool context. Scenario destinations and
failure variants are authored intent; their counts describe dataset composition

Open `demo/experiment.html` for a real saved local-model pilot: script scores,
source failures, exact prompt delta, all eight pairs and the rejected candidate.
Mock tools/users and four working families do not establish statistical gain

The fictional documentation assistant exposes invented facts, skipped lookups and
wrong-version answers. Citation linkage and comparison arithmetic run in-browser;
semantic judgments, references and candidate answers are authored fixtures. They
demonstrate the workflow, not measured model improvement or validated judge accuracy

## Run the Python workflow

[Quickstart](docs/quickstart.md) covers the first local example.
[Package checks](docs/package-checks.md) covers release verification.
[Browser demo](web_demo/README.md) shows how to rebuild the static example

- Import your own traces using [a YAML mapping](docs/trace-import.md)
- Define built-in or custom [criteria and graders](docs/criteria.md)
- Preserve blind [annotation and evidence](docs/annotation.md)
- Compare [judge and reference decisions](docs/judge-metrics.md)
- Turn failures into [actions](docs/failure-actions.md)
- Run a checkpointed [LangGraph replay](docs/workflow.md) or a bounded
  [Inspect experiment](docs/experiment.md) against a caller-managed localhost model

Deterministic stages remain ordinary code. Models, tools, prompts, datasets,
graders, reports and decisions retain their identities; repeated variants are
clustered by task and family. Negative or inconclusive results are retained

No training, external writeback or automatic intervention is included. Stateful
environments and small-model SFT/RL are later work. See [provenance](docs/provenance.md)
'''

CURRENT_README='''# Agent Improvement Lab

An interactive case study and research prototype for LLM assistant improvement.
Find failures, choose a concrete change and compare the next version.
Evaluation reliability comes first.

```
your traces + product rules
→ evaluation → human validation → failure evidence
→ proposed change → human choice → repeated version comparison
```

A failure may call for a prompt change, better context, a tool fix, a different
scaffold, retrieval or training. The library helps collect evidence for that
choice; it does not train models or automatically apply changes.

## Browser example

Follow traces through failure analysis, a prompt change and verification:
**58.7% → 86.5%** on 252 paired tasks in 84 families. This synthetic replay
combines recorded and authored trajectories; the displayed rates illustrate the
workflow. [Data sources and measurement details](docs/provenance.md).

See [the browser guide](docs/public-demo.md) and [build instructions](web_demo/README.md).
The site replays saved data without model or API calls.

## What works now

Start with the bundled case studies. Using the Python components with another
assistant requires a trace mapping, task rules and grader configuration.

- Import JSON/JSONL traces with [a schema mapping](docs/trace-import.md).
- Use six built-in or custom [criteria](docs/criteria.md), including explicitly
  loaded deterministic checkers and saved LLM rubric assessments.
- Review cases in a durable [blind annotation interface](docs/annotation.md).
- Inspect [judge coverage, agreement and errors](docs/judge-metrics.md).
- Draft evidence-bound [failure-to-action proposals](docs/failure-actions.md).
- Run [LangGraph annotation, plan and approval checkpoints](docs/workflow.md).
- Compare saved results or run bounded [Inspect experiments](docs/experiment.md).

The library can prepare provider-neutral judge requests; it does not run a live
judge automatically. Experiments use controlled tools and a caller-managed model
endpoint. Offline examples need no endpoint. Repeats and paraphrases share tasks
and families; working and heldout results stay separate.

## Try the offline workflow

[Quickstart](docs/quickstart.md) covers Python 3.12, exact dependency versions,
installation and runnable examples. The [documentation assistant](docs/docs-answer-demo.md)
reuses the workflow in a second domain. Its traces, semantic assessments and
candidate answers are authored test fixtures; human mode creates an empty queue.

Serve the bundled static site from this snapshot:

```sh
python3 -m http.server 8782 --bind 127.0.0.1 --directory demo
```

Open `http://127.0.0.1:8782`. Results exposes all pairs and six criteria;
`guided.html` preserves the authored documentation workflow and `pilot.html`
the earlier rejected pilot.

Licensed under [MIT](LICENSE).

'''


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def safe_file(root,name):
    path=root/name
    ancestors=[path,*path.parents[:len(Path(name).parts)-1]]
    if not path.is_file() or any(p.is_symlink() for p in ancestors) or not path.resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe release input: '+name)
    return path


def stage(output,demo=None):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a fresh release snapshot directory')
    for name in EXTRA:safe_file(ROOT,name)
    if demo is not None:
        demo=Path(demo).resolve()
        demo_names=set(p.name for p in demo.iterdir() if p.name!='.DS_Store')
        if demo_names not in [set(BASE_DEMO),set(CATALOG_DEMO),set(LEGACY_DEMO),set(FAMILY_DEMO),set(DEMO)]:raise ValueError('Demo inventory differs from explicit allowlist')
        for name in demo_names:safe_file(demo,name)
        manifest=json.loads((demo/'DEMO-MANIFEST.json').read_text())
        if manifest['kind'] not in ['unpublished_static_fixture_demo','unpublished_static_demo'] or manifest['model_calls']!=0 or manifest['real_human_labels']!=0:raise ValueError('Unexpected demo provenance')
        has_experiment='experiment.html' in demo_names
        if has_experiment and (manifest['kind']!='unpublished_static_demo' or manifest.get('includes_saved_experiment') is not True or manifest.get('saved_model_requests')!=52):raise ValueError('Saved experiment provenance missing')
        if not has_experiment and manifest.get('includes_saved_experiment'):raise ValueError('Absent experiment claimed')
        has_family=demo_names in [set(FAMILY_DEMO),set(DEMO)]
        if has_family and (manifest.get('includes_family_experiment') is not True or manifest.get('saved_family_records')!=504 or manifest.get('family_units')!=84):raise ValueError('Family experiment provenance missing')
        if not has_family and (manifest.get('includes_family_experiment') or any(k in manifest for k in ['saved_family_records','family_units'])):raise ValueError('Absent family experiment claimed')
        has_replay=demo_names==set(DEMO)
        if has_replay:
            binding=json.loads((demo/'curated-transfer.json').read_text())
            if not manifest.get('includes_curated_replay') or manifest.get('curated_inferential_result') is not False or binding.get('inferential_result') is not False or binding.get('actor_calls')!=0:raise ValueError('Curated replay provenance differs')
            if manifest.get('curated_authored_traces')!=binding['authored_traces'] or manifest.get('curated_recorded_traces')!=binding['recorded_traces']:raise ValueError('Curated source counts differ')
            if any(digest(demo/name)!=sha for name,sha in binding['files'].items()):raise ValueError('Curated data binding differs')
        elif manifest.get('includes_curated_replay'):raise ValueError('Absent curated replay claimed')
        if set(manifest['files'])!=demo_names-{'DEMO-MANIFEST.json'}:raise ValueError('Incomplete demo binding')
        for name,digest_value in manifest['files'].items():
            if name not in DEMO or digest(demo/name)!=digest_value:raise ValueError('Changed demo asset')
    files=offline.stage(output)
    for name in EXTRA:
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target);files[name]=digest(target)
    if demo is not None:
        for name in sorted(demo_names):
            target=output/'demo'/name;target.parent.mkdir(exist_ok=True);shutil.copyfile(demo/name,target);files['demo/'+name]=digest(target)
    readme=README if demo is not None else README.replace('Open `demo/index.html` for the bundled static browser example.','Build the static browser example using the documented browser builder.')
    catalog_paragraph='Open `demo/catalog.html` to browse 30 curated working scenarios and 180 scripted\nrouting traces, with filters and full tool context. Scenario destinations and\nfailure variants are authored intent; their counts describe dataset composition\n'
    if demo is None:readme=readme.replace(catalog_paragraph,'The browser builder also exports a curated routing catalog with filters and full\ntool context. Its destinations and variants are authored scenario intent\n')
    elif 'catalog.html' not in demo_names:readme=readme.replace(catalog_paragraph,'')
    experiment_paragraph='Open `demo/experiment.html` for a real saved local-model pilot: script scores,\nsource failures, exact prompt delta, all eight pairs and the rejected candidate.\nMock tools/users and four working families do not establish statistical gain\n'
    if demo is None:readme=readme.replace(experiment_paragraph,'The browser builder includes a checked saved local-model experiment with script\nscores, source evidence, prompt delta and a rejected candidate; no model executes\n')
    elif 'experiment.html' not in demo_names:readme=readme.replace(experiment_paragraph,'')
    if demo is not None and 'story.js' in demo_names:
        readme=readme.replace('Open `demo/index.html` for the bundled static browser example. Run the baseline,\ninspect evidence, replay or reject the supplied candidate, then generate test inputs.\nNo installation, provider or network is needed for the built browser assets','Open `demo/index.html` for the saved improvement story on 252 paired\nsynthetic tasks in 84 families. Inspect the criteria and source-bound evidence.\nServe the static assets over localhost or static hosting')
        readme=readme.replace(experiment_paragraph,'Open `demo/experiment.html` for all 252 paired outcomes, six criteria and\nsource-bound evidence. `demo/pilot.html` preserves the earlier rejected pilot;\n`demo/guided.html` retains the authored NovaDocs workflow and personal proposals\n')
        if 'curated-story.json' in demo_names:
            readme=readme.replace('Open `demo/index.html` for the saved improvement story on 252 paired\nsynthetic tasks in 84 families. Inspect the criteria and source-bound evidence.\nServe the static assets over localhost or static hosting','Open `demo/index.html` for the synthetic improvement workflow. It uses\n370 recorded trajectories plus 134 authored successful trajectories on\n252 paired tasks. Shared fixture repairs raise both versions for presentation.\nIts displayed rates are descriptive, not a new measured model gain.\nNo live model runs. Serve the assets over localhost or static hosting')
    if demo is None:
        readme=CURRENT_README.replace('Serve the bundled static site from this snapshot:\n\n```sh\npython3 -m http.server 8782 --bind 127.0.0.1 --directory demo\n```\n\nOpen `http://127.0.0.1:8782`. Results exposes all pairs and six criteria;\n`guided.html` preserves the authored documentation workflow and `pilot.html`\nthe earlier rejected pilot.','Build the static site following [web_demo](web_demo/README.md), then serve the\nnew output directory over localhost. Results exposes all pairs and six criteria;\n`guided.html` preserves the documentation workflow and `pilot.html` the earlier pilot.')
    elif demo_names==set(DEMO):
        readme=CURRENT_README
    (output/'README.md').write_text(readme)
    files['README.md']=digest(output/'README.md')
    document=json.loads((output/'STAGING-MANIFEST.json').read_text());document.update(kind='unpublished_release_preparation',files=files)
    (output/'STAGING-MANIFEST.json').write_text(json.dumps(document,indent=2)+'\n')
    public_files={**files,'STAGING-MANIFEST.json':digest(output/'STAGING-MANIFEST.json')}
    manifest={'kind':'unpublished_release_preparation','files':dict(sorted(public_files.items())),'license':'MIT','repository':'pending','hosting':'pending','publication':False,'includes_built_demo':demo is not None,'includes_catalog':demo is not None and 'catalog.html' in demo_names,'includes_saved_experiment':demo is not None and 'experiment.html' in demo_names,'includes_family_experiment':demo is not None and 'story.js' in demo_names}
    (output/'RELEASE-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--demo');args=parser.parse_args()
    try:result=stage(args.output,args.demo);print(json.dumps({'files':len(result['files']),'published':False,'demo':result['includes_built_demo']}))
    except (ValueError,KeyError) as error:parser.exit(2,str(error)+'\n')
