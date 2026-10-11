# Documentation assistant example

A second domain uses the same trace import, annotation, judge-validation,
failure-action and saved comparison interfaces. It answers questions about
fictional NovaDocs v2 using `search_docs` and `read_doc` mock tools

The example contains 16 authored traces: four task families, two repeats per
version. Three baseline defects are deliberate: an invented retention period,
a skipped document lookup and an answer based on the wrong documentation version

## Run the complete fixture replay

Complete [Quickstart](quickstart.md) first, then use its `ail_python` variable
from the unpacked repository root. Choose a fresh output directory:

```sh
"$ail_python" examples/docs-answer/run_demo.py --output /tmp/ail-docs-demo
```

Read `RESULT.md` first. Then inspect:

- `criteria-baseline/report.md`: six built-in criteria and custom citation linkage
- `workflow/actions.md`: observations, cause hypothesis, proposed change and verification
- `workflow/drafts/ground-current-docs.md`: the concrete product change draft
- `workflow/comparison.md`: paired replay with repeats averaged by task and family
- `inputs/provenance.json`: data and assessment origins

The script automatically advances **fixture** annotation and approval checkpoints.
It names the fixture actor `synthetic_test_fixture`; the real annotation database
stays empty. Every candidate repair and semantic assessment was authored in advance.
The displayed difference tests workflow behavior, not model improvement or judge reliability

## Open a real blind annotation queue

```sh
"$ail_python" examples/docs-answer/run_demo.py --human --output /tmp/ail-docs-human
"$ail_python" -m agent_improvement_lab.annotation serve --db /tmp/ail-docs-human/workflow/annotations.sqlite3 --port 8780
```

Open `http://127.0.0.1:8780`. The queue has eight baseline traces without versions,
judge decisions or hidden prompts. User messages can be attached as evidence alongside
an assistant anchor for an incorrect verdict; tool evidence is supplemental.
Decisions and revisions survive reload

After the queue is complete, use the [workflow commands](workflow.md) for
`labels-ready`, `propose`, and an explicit approval or rejection. This example
has no heldout families and no claim of generalization. `--human` computes authored
criterion reports first, then pauses the workflow before analysis of human decisions;
it never copies fixture decisions into the database

## Adapt the input or criteria

`inputs/baseline-raw.json` and `.jsonl` contain the same nested vendor-style records.
`mappings/docs-answer.yaml` maps requests, message order, tool calls, results and
provenance into the shared sequential schema. `configs/docs-answer.en.yaml`
selects the built-in criteria and one custom deterministic grader

The custom grader is explicitly loaded from `examples/docs-answer/citation_plugin.py`.
It only checks whether each citation points to a successfully read document before
this answer. A valid citation can still accompany a false claim or wrong-version answer;
`unsupported_claims` and `context_use` assess those separately

The grader applies to the tiny demo's factual textual answers. It is not a general
citation validator, retrieval benchmark or entailment model. Semantic decisions use
the existing saved-assessment adapter; no model or external service is called

Complex spans, parallel tools, real document retrieval and live model generation
are outside this example. Fresh installation instructions are in [quickstart](quickstart.md)
