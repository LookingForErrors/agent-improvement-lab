# Run an approved experiment

`ail-workflow experiment` generates paired trajectories and evaluates them.
The original `ail-workflow start` route still reviews and compares saved data

The execution route uses Inspect AI for model/tool turns and the existing criteria
engine for grading. Selection, approval, budgets, persistence and reports are
ordinary Python. It does not load models or change real tickets

## Try it without a model

Install the combined environment from [Package checks](package-checks.md) first.
Use its `ail_python` variable from the repository root and choose fresh directories:

```sh
"$ail_python" examples/routing-experiment/build_demo.py --output /tmp/ail-experiment-inputs
"$ail_python" -m agent_improvement_lab.workflow_cli experiment prepare --config /tmp/ail-experiment-inputs/experiment.yml --workspace /tmp/ail-experiment
```

Inspect the copied action, evidence, prompts, criteria, model configuration and
limits. Copy the printed `manifest_sha256` into an explicit decision:

```sh
"$ail_python" -m agent_improvement_lab.workflow_cli experiment approve --workspace /tmp/ail-experiment --manifest-sha256 <hash> --reviewer <your-name>
"$ail_python" -m agent_improvement_lab.workflow_cli experiment run --workspace /tmp/ail-experiment
"$ail_python" -m agent_improvement_lab.workflow_cli experiment status --workspace /tmp/ail-experiment
```

Open `REPORT.md`, `TRACES.md`, `comparison.md`, `uncertainty.json` and
`registry.json`. This fixture prescribes one improvement and performs **zero real
model requests**. It demonstrates execution and reporting, not prompt quality

Check the available execution commands:

```sh
"$ail_python" -m agent_improvement_lab.workflow_cli experiment --help
```

## Continue from failure analysis

The existing import, blind annotation, judge-validation and failure-action tools
produce a validated `triage.json` and curated `plan.json`. Export one action:

```sh
"$ail_python" -m agent_improvement_lab.workflow_cli experiment select --triage <triage.json> --plan <plan.json> --action-id <id> --workspace /tmp/ail-selected-action
```

Point the experiment YAML's `action`, `evidence` and `evidence_traces` fields to
that export. Selection preserves source hashes and original evidence steps. It
does not authorize inference; preparation and exact-manifest approval follow

Executable actions must name the same primary and regression criterion IDs as
the criteria pack and decision rule. This executor supports prompt/context/
scaffold changes for assistant failures in the controlled routing environment.
Scenario defects and unassessable measurements need investigation first

Built-in routing graders check confirmed assignment, destination, inspected
supporting fields and the narrow tool contract. For custom criteria, provide
self-contained trusted grader code explicitly with `prepare --plugin <file.py>`.
The plugin is copied and sealed. This executes trusted Python, not a sandbox;
YAML never imports grader code. Saved LLM judge validation remains in the replay
and criteria tools; this execution route makes no judge calls

## Use an existing local endpoint

Set `backend: local_http`, remove `fixture_outputs`, and supply recorded working
failure evidence plus a `recorded_evaluation` criteria pack. Configure the model
ID/version, prompts, cases, repeat count, tool policies and exact budgets

For `local_http`, use an explicit `http://127.0.0.1:<port>/v1/chat/completions` endpoint.
The caller prepares and owns that server/model. The runner does not load/unload
models, download weights, install packages or stop the caller's server. It checks
available RAM and swap growth from a common execution baseline

Model version and context are **caller attestations**, not independently verified
load settings. The manifest records that limitation; raw provider requests and
responses remain available. Seeds are requested, not a reproducibility guarantee

The optional `execution` extra declares the already-used pinned Inspect/psutil
versions. This change installs nothing. The current checkout verifies replay and
execution in their existing separate runtimes. On 2026-10-09 the common execution
route completed eight recorded local-model trajectories through mocks; report-only
re-rendering made no additional model requests. The combined installation and offline checks are recorded in [Package checks](package-checks.md)

The packaged `polza` adapter is a fixed experimental remote route. It requires
separate credentials, explicit approval and a frozen request/cost budget. It is
POSIX-only; the current offline release check makes no provider requests and does
not verify remote service availability or prices

## Stop, recover and decide

- Use `reject` with the exact manifest hash to decline a prepared run
- HTTP starts are reserved durably before sending, including failed starts
- Requests have no automatic retry. Completed slots never execute again
- An interrupted started slot is unknown; `resume` may continue only unstarted
  slots under the original deadline, baseline, ledger and approval
- The parent supervises deadline/memory; a worker exits if its parent disappears.
  Locking/process supervision currently requires POSIX, tested on macOS
- Once finalization starts, resume writes reports without additional generation
- `report` repeats deterministic reporting without model calls. Changed inputs,
  runtime, implementation or completed raw evidence fail verification

Missing/unassessable pairs produce `inconclusive_measurement`. Regressions produce
`do_not_adopt`; equal results produce `no_observed_improvement`; improvement
without regressions produces `advance_to_larger_test`. All are terminal results.
No decision automatically promotes a version

## Optional recovery of an unassigned text turn

In this closed environment, clarification must use `ask_user`. If an assistant
returns text with no tool call while the ticket is unassigned, an optional
scaffold control can request continuation once per trajectory:

```yaml
zero_call_recovery:
  baseline: 0
  candidate: 1
```

Both values default to `0`; only `0` or `1` is accepted. Keep the prompts,
tools, authored replies and auto tool mode identical when testing this change

The original assistant output stays in the trace. Recovery adds a recorded
control instruction, not a user answer or tool result. For providers whose
template accepts only an initial system message, that control is merged into
the initial system instruction in subsequent requests. A per-request
`control-projection.json` preserves the chronological messages and binds their
effective request hash. The dialogue report shows the control explicitly

The next generation consumes the existing generation, request and time limits.
There is no budget reset or started-slot replay. A second unassigned text turn
ends the trajectory; post-assignment text and native tool calls use the original
behavior. An unsupported native route is still a routing failure

This option has offline scripted verification. Its effect with a live model is
unmeasured. A free-text clarification can be valid in an ordinary conversation;
this control is for the declared tool-mediated interaction contract

Paired results average repeats by task and tasks by family. Fewer than ten paired
families return an explicit unestimable interval; larger sets receive a descriptive
family bootstrap interval. The ten-family guard does not establish adequate power.
Question/field consistency and broader semantic quality are not covered by the
narrow routing checks. Authored facts and fixture labels are not human gold

## Optional evidence-first scaffold

A single whole-response JSON or bare Markdown fence is accepted as a
presentation wrapper. The entire inner object still passes strict parsing,
schema and exact observed-quote checks. Raw planner output is retained.

A sample wall timeout ends only that slot and retains an unassessable
measurement, without retrying it. Later slots may continue. Overall wall,
HTTP-start and memory guards still stop the whole queue.

`evidence_first: {baseline: false, candidate: true}` adds a preliminary model
analysis after a successful ticket read and before each pre-assignment action.
Both versions retain the same actor prompt, tools and controlled user facts.
The planner sees only already recorded user/tool text with original step IDs,
never unrequested facts, expected destinations or grader parameters

Exact quoted substrings, references and a closed JSON structure are checked in
ordinary code. A valid structure does not establish semantic correctness. Raw
analysis and validation are retained under each slot's `planner/`; events in trace
metadata link these records. Planner output is not a user reply, tool result,
human label or additional fact. Only a validated plan is supplied as explicitly
untrusted preliminary context in the actor's provider request. A rejected plan
remains recorded; the actor uses original evidence without a repair retry

Planner and actor share the same request, generation, sample and overall limits.
At least two remaining generation attempts are required to begin a planning/action
pair. No planning occurs before the ticket read or after assignment. Planning
never executes tools; the actor selects the actual tools and arguments. This
mode and zero-call recovery are mutually exclusive, default-off interventions

The fixture contracts exercise structure, projection, budgets and provenance;
they demonstrate no measured model improvement. A live candidate requires a
fresh frozen protocol, inventory, budget and independent pre-run review. The
small working discovery cannot support a statistical or production gain claim
