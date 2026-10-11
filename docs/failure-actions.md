# From trace findings to proposed improvements

This block turns reviewed trace findings into evidence-bound **local task drafts**.
Review evidence, record a cause hypothesis and define a verification plan.
An analyst chooses the intervention; generating drafts makes no model calls.

## Run the supplied example

```sh
python examples/docs-answer/run_demo.py --output /tmp/ail-actions-source
```

Read `/tmp/ail-actions-source/workflow/actions.md` and
`workflow/drafts/ground-current-docs.md`: observed invented facts, omitted reads
and wrong-version answers → evidence → unverified hypothesis → a product prompt
proposal with fixed controls, repeat count and regression checks

The traces, references and analyst proposal are authored fixtures. Two copies
count as two traces, one task and one family. This does not measure model benefit

The separate action CLI can reproduce that draft with fresh outputs:

```sh
python -m agent_improvement_lab.failure_actions prepare \
  --traces /tmp/ail-actions-source/inputs/baseline-normalized.jsonl \
  --human /tmp/ail-actions-source/inputs/fixture-annotations.json \
  --rubric /tmp/ail-actions-source/inputs/rubric.txt \
  --dataset-version docs-answer-fixture-v2 --data-kind test_fixture \
  --output-dir /tmp/ail-triage
python -m agent_improvement_lab.failure_actions report \
  --triage /tmp/ail-triage/triage.json \
  --plan /tmp/ail-actions-source/inputs/plan.json \
  --output-dir /tmp/ail-actions-report
```

Prepare writes `triage.json`, `issues.json` and a deliberately incomplete
`plan-template.json`. For your own data, fill observation, optional hypothesis,
intervention, recipient, proposed change and verification. A stale or incomplete
plan cannot produce an accepted action report

Report writes JSON, Markdown and one proposed task per action under `drafts/`.
These are local drafts with `draft_pending_human_selection`; no external issue,
intervention or training run is started. Selection and a bounded experiment are
separate checkpoints in the [workflow](workflow.md)

```sh
python -m unittest discover -s tests -p test_failure_actions.py
```

## Evidence and attribution

Input is a complete declared normalized trace inventory plus a **private exported
annotation file snapshot**, using annotation export-private. The module never
reads the live SQLite workspace. Source hashes match annotation's canonical
normalized Trace dump, including defaults. Rubric, trace/task/family/split and
latest label revision must agree. Initial context/original step maps and labels
are revalidated through the existing annotation projection and validation code.

An assistant failure requires the human reference verdict `incorrect` and an
eligible evaluated assistant evidence step. Tool evidence can support it but
cannot alone assign blame. Initial authored assistant/user context cannot be
blamed on the current model. Recorded tool OK is accepted only for the action
it reports under the existing MVP trust boundary; this block does not verify
hidden backend state.

Scenario flags create separate scenario-defect findings, including when the
assistant verdict is correct. Unassessable creates an evaluation-gap finding,
not an automatic assistant failure. One annotated trace can create several kinds
of issue, so issue counts differ from verdict and trace counts. Unannotated
traces remain missing, never automatically passing. Error types are reference
labels, not root causes.

Every working label is preserved, including labels with no issue or selected
proposal: assessor, revision, timestamp, comments, error types, public/original
step linkage and hashes. Action evidence includes original messages/tool calls
and results from selected steps. New source or annotation revision changes the
triage/issue identities and invalidates a stale plan.

## Planning contract

The analyst supplies the observed summary and optional cause hypothesis.
A hypothesis is always marked unverified; null explicitly means unknown. The
module validates references and structure, not the semantic truth of a finding
or whether a proposed experiment will help. An independent human checks meaning.

Interventions: prompt, context, tools, scaffold, retrieval, model, training_data,
post_training, scenario_data or investigation. Recipients: product, backend, ML
or evaluation. Model/training proposals route to ML, but no bad score or error
label automatically creates a training recommendation.

Scenario defects route to evaluation with scenario_data or investigation.
Evaluation gaps permit investigation for evaluation/backend first. They cannot
be converted directly into an assistant-training proposal. Mixed issue kinds
must use separate actions. Several alternative proposals may refer to the same
issue; the human chooses among them.

Verification must match the action:

| Proposed action | Protocol | What completion means |
|---|---|---|
| Assistant intervention | working_selection_then_family_heldout_check | Compare before/after on the same declared tasks with at least two repeats, agreed primary measure, controls and regression checks. |
| Scenario revision | scenario_revision_review | Review the new scenario version; preserve original source, labels and split assignments. No assistant-performance claim. |
| Investigation | evidence_investigation | Link recording/tool/termination evidence; report a supported diagnosis or unresolved gap. No promise of repair. |

Repeat counts apply to assistant comparisons; the other two protocols use null.
Every action requires a primary measure, concrete completion criterion,
regression checks and fixed controls. Schema validation cannot guarantee the
text is scientifically sufficient; final human review remains required.

## Heldout and privacy

Only working findings can be referenced in plans. Heldout identifiers, request
text, label comments and step evidence are omitted from prepared artifacts and
reports; only aggregate coverage/counts remain. A plan cannot reference a heldout
issue through this module. Human-authored free text and out-of-band access still
require discipline; this is not a sandbox that prevents an analyst from manually
copying information. Freeze selection on working data before a later separately
run heldout check.

Prepared bundles are trusted analyst snapshots. Revalidation checks their retained
source/label links and feasible working counts, but opaque full-inventory hashes
cannot prove omitted source data without the original files. Declared provenance
and analyst text are not authenticated evidence. Use `test_fixture` for QA and
`human_reference` only for actual human reference inputs; neither promotes labels
to accepted gold or proves a cause.

All artifacts are analyst-only and may contain private source/label information.
Never connect them to the blind annotation UI/API/export or publish them without
review. Outputs use fresh paths and staged writes; existing output directories
are rejected. Rollback preserves human snapshots, live annotation data and all
unrelated work.
