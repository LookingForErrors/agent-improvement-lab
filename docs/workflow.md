# Local improvement replay workflow

The workflow connects existing modules through LangGraph and SQLite checkpoints:

```
JSON/JSONL + saved judge assessments
→ import, validate, freeze identities, sample working traces
→ STOP: blind annotation
→ human/judge report + evidence-bound failure triage
→ STOP: curate an improvement plan
→ action drafts
→ STOP: approve one action and an exact candidate replay, or reject
→ paired comparison of saved baseline/candidate results
```

Install the workflow environment using [Quickstart](quickstart.md) first.
The commands below use its `ail_python` variable and run from the unpacked
repository root. The replay makes no model calls. The candidate and judge outputs are supplied in advance;
approval compares those saved outputs, rather than applying an intervention.
Installation checks and their scope are recorded in [Package checks](package-checks.md).

## Run the authored demo

From the repository root, choose **new** input and workspace directories:

```sh
"$ail_python" examples/routing-loop/build_demo.py --output /tmp/ail-demo-inputs
"$ail_python" -m agent_improvement_lab.workflow start --config /tmp/ail-demo-inputs/config.yaml --workspace /tmp/ail-demo-run
"$ail_python" -m agent_improvement_lab.workflow labels-ready --workspace /tmp/ail-demo-run
"$ail_python" -m agent_improvement_lab.workflow propose --plan /tmp/ail-demo-inputs/plan.json --workspace /tmp/ail-demo-run
```

The last command prints the approval checkpoint's `identity`. Copy its two hashes:

```sh
"$ail_python" -m agent_improvement_lab.workflow approve --workspace /tmp/ail-demo-run --reviewer demo-reviewer --action-id role-routing --plan-sha256 PLAN_HASH --candidate-sha256 CANDIDATE_HASH
"$ail_python" -m agent_improvement_lab.workflow status --workspace /tmp/ail-demo-run
```

Use `reject` with the same identity arguments to end the process without comparison.
Review `comparison.md`, `judge-validation.md`, `actions.md` and `drafts/role-routing.md`
in the workspace. Keep the whole workspace to preserve provenance.

All demo trajectories, judge decisions and reference decisions are **authored test
fixtures**. No model or human generated them. The reference adapters use the existing
human snapshot schema only to exercise joins; `data_kind: test_fixture` and assessor
`synthetic_test_fixture` distinguish their meaning. The annotation database starts
empty and remains empty in scripted mode. These results establish workflow behavior,
not judge reliability or product improvement. The unresolved demo request is a compact
pre-authored routing fixture, not a realistic multi-turn support benchmark.

## Use actual traces and human decisions

Create a YAML config using the same fields, set `mode: human`, and remove
`fixture_annotations`. Supply `baseline`, `candidate`, `baseline_judge`,
`candidate_judge`, `rubric` and `dataset_version`. Paths resolve relative to the
config file. `max_cases` bounds human work; `seed` fixes working-family sampling.
Only working cases enter the annotation queue and action evidence. Heldout labels
contribute aggregate replay counts; heldout raw inputs remain private analyst files.

The optional `mapping` names a simple YAML mapping supported by the
[importer](trace-import.md). The same mapping applies to both arms. Judge assessments
must bind to the resulting **normalized** trace hashes, including imported source
provenance, rather than raw input hashes. Invalid imports fail before workspace creation.

Required comparison metadata:

- Unique trace IDs, one explicit `agent_version` per arm, and different arm versions.
- Explicit `task_id`, `family_id`, `split`, and string `metadata.repeat_id`.
- At least two repeats for every task; identical task/family/split/repeat inventories.
- Identical authored non-system context in each pair, marked `input` in
  `metadata.message_sources`. Generated clarifying replies may differ between arms.
- Complete saved `judge-assessments-v1` coverage, source/rubric hashes and judge identity.
  Model, version, prompt and config hashes are fixed; run IDs may differ.

The blind viewer supports sequential textual content/reasoning and explicit
message origins. A trusted system line `Current ticket: ID` is optional.
The [documentation assistant example](docs-answer-demo.md) uses the same projection
without routing context. Parallel spans and rich message content are deferred.

Start the workflow, then open its own blind queue:

```sh
"$ail_python" -m agent_improvement_lab.annotation serve --db /tmp/ail-demo-run/annotations.sqlite3 --port 8780
```

Annotate every selected case at `http://127.0.0.1:8780`, then use `labels-ready`.
Human names, revisions, evidence, comments and source hashes become a frozen analyst
snapshot. Missing selected labels stop the process. AI proposals must be reviewed
separately; they are never inserted into this human database by the workflow.
Use a new workspace for a new snapshot after revising an already frozen decision.

Edit a copy of `plan-template.json`: fill the analyst, working issue references,
observed failure, unverified cause hypothesis, proposed change and verification.
The existing [failure-action validator](failure-actions.md) rejects unknown or heldout
evidence and mixed assistant/scenario/evaluation issues. A reviewer selects one
assistant comparison action. This replay supports its declared repeat count but does
not automatically interpret its free-text success criterion or promote a candidate.

## Reliability and limitations

`manifest.json` seals copies of source inputs, normalized traces, rubric, saved judges,
sampling configuration, engine source and runtime versions. Source originals are
recorded for provenance; subsequent edits to originals do not change sealed copies.
Every resume checks sealed files and checkpoint-bound outputs. Approval records the
reviewer, selected action, plan/candidate hashes, manifest and human snapshot hashes.
Human-mode resumes additionally reject changes to the latest annotation revisions.

SQLite persists graph checkpoints. Deterministic artifact writes are exclusive and
idempotent for identical bytes; a conflicting or partial file stops execution rather
than silently replacing evidence. After a process error, use `resume`; for a human
checkpoint, use its named command. A crash after comparison writes can safely retry
the same local report. Use one local operator per workspace. This is not a remote
job executor or an exactly-once guarantee for external effects.

The paired report retains unassessable verdicts and their counts. Its declared correct
delta averages repeats per task, then tasks per family, then families equally.
Unassessable contributes zero to the correct indicator; it is not silently dropped.
Working and heldout are separate. No confidence intervals, calibrated judge,
representative accuracy, causal attribution or automatic acceptance are claimed.
Human/judge reports expose coverage, three-class confusion/precision/recall/F1,
agreement, slices and descriptive family-cluster bootstrap intervals with warnings.
An authored demo or a small targeted human queue cannot close the scientific gates.

No UI serves judge output, versions or source metadata. Analyst artifacts contain
private traces and annotations: they are not automatically publishable. Public
examples contain synthetic fixtures and no private annotations.

Validation: eight isolated workflow scenarios cover checkpoints, complete replay,
rejection, stale approval/evidence, incomplete pairing/judges, invalid plans,
crash/resume, real annotation revisions, sampling and fixture separation.
