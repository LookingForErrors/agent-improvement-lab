# Product criteria and custom graders

A **criterion** defines what to assess, when it applies and what evidence is needed.
A **grader** produces a decision. A **metric** aggregates decisions with a declared
denominator. These product measurements are separate from the existing
[human/judge reliability reports](judge-metrics.md).

## Baseline catalog

| ID | Question | Applicability | Evidence and example |
|---|---|---|---|
| `task_completion` | Was the authorized objective or permitted handoff reached? | A task exists; missing objective is unassessable | Objective, policy and assistant/tool steps. Returned tool success confirms observed execution, not independent backend truth. |
| `tool_selection` | Were relevant required tools used in permitted order? | Tools used **or required** | Rules and calls/complete trace. Required call omitted is incorrect, not N/A. Tool error alone is not wrong selection. |
| `tool_arguments` | Were arguments structurally valid and semantically justified? | At least one call | Schema, arguments and context. Correct schema with wrong ticket ID is incorrect. Structural checking alone is insufficient. |
| `context_use` | Were relevant facts, instructions and corrections respected? | Relevant context exists | Context and decision. A stale ID used after an authorized correction is incorrect. |
| `clarification` | Were questions and handoff appropriate? | Clarification needed **or asked** | Uncertainty, policy, questions/replies. Unnecessary question can fail; permitted unresolved handoff can pass. Budget is product-specific. |
| `unsupported_claims` | Were assertions and commitments supported? | Externally checkable claim/commitment exists | Claim plus task/tool/policy evidence. Invented success or SLA can fail separately from task completion. Soft wording follows actual product policy. |

Definitions/rubrics have versions. `criteria catalog` prints the exact machine-readable
catalog. Built-ins provide assessment definitions, **not universal truth or a trained
judge**. Semantic baseline demo decisions are saved authored fixtures, not real LLM outputs.

## Outcomes and denominators

The full expected trace × criterion matrix is retained:

- `assessed`: verdict `correct`, `incorrect` or `unassessable`.
- `not_applicable`: no verdict; applicability was evaluated and explained.
- `missing`: no assessment was supplied for the expected cell.
- `grader_error`: execution, evidence or saved-binding failure; no assistant verdict.

An unassessable outcome differs from N/A: the criterion is relevant but evidence cannot
support a quality conclusion. Decisions need explanations and unique existing evidence
steps. Correct/incorrect decisions require trace evidence; missing required actions may
be established by complete-trace/context evidence rather than a nonexistent call step.
The criterion hash binds the assessment rubric; the trace hash binds supplied
product rules, tool contracts and context. Every result carries both hashes.

`correct_rate_among_assessable = correct / (correct + incorrect)`.
Zero denominator is undefined. Read it beside expected population, all outcome counts,
decision coverage, and `unassessable_rate_among_assessed`. Missing/error cases are not
silently failures or successes. JSON also reports equal-family/task means: average
available assessable repeats within task, task means within family, then families equally.
Tasks/families without assessable observations are counted separately.

Working and heldout are separate. These are descriptive measurements; no confidence
interval, significant improvement or automatic acceptance is claimed. Reliability and
sample-size work are later tasks. Optional recorded reasoning is input evidence; hidden
reasoning is not required.

## Run the two authored examples

Complete [Quickstart](quickstart.md) first. Use its `ail_python` variable from the repository root and choose fresh paths:

```sh
"$ail_python" examples/criteria-demo/build_demo.py --output /tmp/ail-criteria-inputs
"$ail_python" -m agent_improvement_lab.criteria evaluate --traces /tmp/ail-criteria-inputs/baseline.json --config /tmp/ail-criteria-inputs/base.yaml --assessments /tmp/ail-criteria-inputs/base-baseline-assessments.json --output /tmp/ail-base-before
"$ail_python" -m agent_improvement_lab.criteria evaluate --traces /tmp/ail-criteria-inputs/candidate.json --config /tmp/ail-criteria-inputs/base.yaml --assessments /tmp/ail-criteria-inputs/base-candidate-assessments.json --output /tmp/ail-base-after
"$ail_python" -m agent_improvement_lab.criteria compare --baseline /tmp/ail-base-before/report.json --candidate /tmp/ail-base-after/report.json --output /tmp/ail-base-comparison
```

Open `report.md`, `metrics.json` and `comparison.md`. Each arm has eight traces,
four tasks/families and two repeats. The fixture intentionally improves role routing,
introduces a wrong ticket and unsupported timing, truncates one call before its
response, omits one assessment and includes one stale binding. It shows quality differences **and measurement defects**. There
are zero model calls, zero human labels and no real services.

Run user-defined criteria without editing core:

```sh
"$ail_python" -m agent_improvement_lab.criteria evaluate --traces /tmp/ail-criteria-inputs/baseline.json --config /tmp/ail-criteria-inputs/custom.yaml --plugin /tmp/ail-criteria-inputs/ticket_plugin.py --assessments /tmp/ail-criteria-inputs/custom-baseline-assessments.json --output /tmp/ail-custom-before
"$ail_python" -m agent_improvement_lab.criteria evaluate --traces /tmp/ail-criteria-inputs/candidate.json --config /tmp/ail-criteria-inputs/custom.yaml --plugin /tmp/ail-criteria-inputs/ticket_plugin.py --assessments /tmp/ail-criteria-inputs/custom-candidate-assessments.json --output /tmp/ail-custom-after
"$ail_python" -m agent_improvement_lab.criteria compare --baseline /tmp/ail-custom-before/report.json --candidate /tmp/ail-custom-after/report.json --output /tmp/ail-custom-comparison
```

`ticket_identity` is deterministic; `reply_grounding` is a custom saved LLM rubric.
Both appear beside the six built-ins. `PROVENANCE.json` identifies all fixture faults.
Installation is covered in Quickstart; these examples use saved assessments and make no live judge calls.

## Configuration and deterministic extension

The generated `base.yaml` selects built-ins. `custom.yaml` shows a complete custom
`definition` with version/title/question/applicability/rubric/target, plus grader
kind, ID and JSON-safe parameters. Choose exactly one built-in or custom definition.
Select one primary and any selected non-primary regression criteria. No combined
universal score is generated.

A trusted Python file exposes `PLUGIN_API_VERSION = 1` and `register(registry)`.
Registration calls `registry.register(id, version, check)`; the function takes
`(trace, parameters)` and returns `Decision` or its validated dictionary. Duplicate
IDs are rejected. The example plugin reads a trusted current-ticket instruction
and checks every ticket-bearing call. It returns N/A for no such call, unassessable
for missing trusted context, and correct/incorrect with call evidence otherwise.

Code is loaded only by explicit `--plugin PATH`; YAML contains registry IDs and
data, never import paths, expressions or commands. **This executes trusted local
Python; it is not a sandbox.** Use self-contained plugins; imported external
dependencies are not sealed by the plugin-file hash. Core module hashes/runtime
versions and the plugin file hash are recorded for comparison.

The built-in `argument_fields` checker is deliberately structural: use it under
a narrow custom definition, not as proof of complete semantic argument correctness.
Unknown registry IDs reject configuration before report creation. A registered
checker exception or malformed decision becomes a per-cell grader error.

## Saved LLM interface

No provider is contacted by this module. Create provider-neutral request artifacts:

```sh
"$ail_python" -m agent_improvement_lab.criteria requests --traces /tmp/ail-criteria-inputs/baseline.json --config /tmp/ail-criteria-inputs/custom.yaml --output /tmp/ail-requests
```

Requests contain the criterion/rubric, trace, response schema and binding hashes.
An external caller can use these to obtain real decisions. The caller must record
actual provider/model/version/prompt/config/run identity; this implementation
does not prove that declared hashes correspond to an actual model invocation.
Request exports contain full supplied traces and are analyst-only for private data.

`criteria-assessments-v1` includes data kind, origin, judge identity mapping and rows:
trace/criterion IDs and hashes, grader ID, exact judge-identity hash, assessor kind
`llm`, and decision. Provider is explicit. Duplicate/foreign rows, unexpected graders
or malformed envelopes fail fast. A stale source/rubric/identity or malformed decision
becomes a grader-error cell. Absent expected rows are missing. Fixture provenance must
be `authored_test_fixture`/`test_fixture`; recorded data uses `recorded_model`/
`recorded_evaluation`. None of these are human gold.

## Comparison compatibility

Reports validate source/criterion bindings and retain the full matrix. Comparison
requires fixed pack/rules/options, engine and grader/plugin/provider/model/prompt/config
identity; run IDs are provenance and may differ. An identity change produces an
`incompatible_measurement` report with **no delta**, rather than calling it improvement.

Compatible arms must have different agent versions and exactly matching
task/family/split/string-repeat IDs, with at least two repeats per task. All fixed authored input (including system/developer facts), embedded task
objective/context/metadata (excluding source-only provenance), trace language and nonempty `metadata.evaluation_context` must match.
Put fixed scenario/tool/environment versions in that context. A variable agent prompt must be a separate message tagged `intervention` in
`metadata.message_sources`; fixed facts/rules stay `input` and cannot change.
The existing routing annotation viewer does not yet accept this extra tag; this
convention belongs to the product-criteria comparison path. These declarations are not independent
verification that an external experiment followed its protocol.

Delta uses jointly assessable matched cells, averaging by task/family. N/A, missing,
errors and unassessable pairs are excluded **and counted with reasons**. Changing
coverage can bias this conditional comparison; full coverage and judge validation
remain necessary for a product conclusion. Reports do not silently intersect
incompatible populations or apply a free-text success gate.

Generated report directories are published after all files are written and never
overwrite an existing path. Human databases and existing annotation workflows are
not modified by these product-criteria reports.
