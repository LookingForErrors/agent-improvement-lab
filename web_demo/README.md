# Synthetic improvement demo

The main page presents a curated replay in five scenes: Before → Find → Change → Verify → After. Play/Pause, manual steps, skip and replay share the same data. Reduced motion uses manual navigation; opening evidence or leaving the tab pauses playback

Demo routing success: **58.7%→86.5%**,148→218 of252 pairs in84families. The presentation dataset combines370recorded trajectories with134authored successful trajectories. Shared fixture repairs raise both versions; six pairs remain unknown. These rates describe the replay and are not a new measured model gain or statistical confirmation

`experiment.html` exposes every pair and all six saved criteria, with criterion-specific source-bound highlights. `guided.html` preserves the authored NovaDocs workflow and browser drafts; `pilot.html` preserves the earlier rejected delivery pilot. The trace catalog remains a separate scripted collection

Rebuild the curated replay without model calls using the workflow environment from [Quickstart](../docs/quickstart.md):

```sh
PYTHONPATH=. python web_demo/build_replay.py --output /tmp/ail-curated-replay
```

The plan fixes22capture-gap pairs and45shared failures before constructing successful trajectories for both arms. Five mechanical checks validate every authored trace. Eleven disputed recorded traces received two fresh blind AI reviews; six disagreements remain. References and authored successes are not human gold

The original recorded fixture, counts, uncertainty and decision remain unchanged in source. The homepage loads a compact curated projection; Results loads full traces. Evidence & limits and exported records describe each source. Static HTTP serving is required

The complete static builder below includes these files. Existing workflow details follow

# Authored workflow example

A static, dependency-free visitor demo for the fictional NovaDocs assistant

The workspace shows one step at a time: Inspect → Diagnose → Propose → Compare.
Start with a request, answer and source beside each other. Four named cases expose
three failures and one successful trajectory; technical IDs and repeated copies
stay in the full-trace disclosure

Diagnosis shows three relevant checks first, with the complete rubric available
in details. The proposal links back to evidence. Comparison keeps task outcomes
beside supplied candidate answers, then offers unscored test inputs for the next
cycle. About the example explains provenance; workspace options contain exports,
theme and reset

**Trace catalog** is a separate workspace with 30 curated working scenarios,
30 families and 180 scripted routing traces. Search requests, selected trace
variants and complete ticket context; filter by conversation style and clarification
count. Each scenario has six authored outcomes. Their counts describe collection
composition, not model failure rates

Open a trace directly into the conversation. User and assistant messages are the
primary layer; linked tool calls and results form compact expandable actions.
Recorded failure evidence is emphasized even when it belongs to a tool. Evidence
links open the relevant action before focusing the original step. Long ticket
histories and exact raw responses stay in disclosures; exported traces retain
their full content. The export icon has a text label for assistive technology. Catalog
filters use a separate browser session key and do not change guided-demo choices

The curation preserves raw corpus files. A separate version repairs first-person
clarification wording, shortens five cluttered opening requests and gives unsupported
completion claims relevant destination-specific wording. The long histories remain
synthetic context stress fixtures. No model or human supplied these trajectories

The public fixture is `catalog-fixture.json`. To rebuild it from the
source checkout’s existing corpus, use a fresh output directory:

```sh
python web_demo/build_catalog.py --dataset datasets/routing-demo-en-v1 --selection web_demo/catalog-selection.json --output /tmp/ail-routing-catalog
```

The public snapshot includes the curated fixture and selection; the larger raw
corpus and heldout scenarios are excluded

**Saved experiment** connects actual saved local-model outputs to four script
criteria, source failures, a tested prompt addition and all eight comparison pairs.
Open Evaluation → Change → Comparison, then inspect any criterion or pair. The
source `criteria-report-v1` cells drive step highlights; the browser does not use
catalog outcome labels for these grades. Questions delivered through `ask_user`
show the assistant question and controlled reply, with original tool records in
details. Trace-specific anchors keep repeated step IDs and prior source IDs separate

The candidate was rejected: destination matches stayed 6/8, assignments rose 6/8
to 7/8 and one wrong route appeared. This is four working families with correlated
repeats, no heldout result or statistical gain. Source outputs are real; tools and
user facts are mocks/authored. No LLM judge or new human gold was used. Saved
measurements are distinct from browser aggregate calculations and future execution

`experiment-fixture.json` contains the saved pilot projection. The complete site
builder below reuses it without rerunning the model

The exporter validates saved reports with the existing criteria engine, reproduces
their comparison/aggregates and checks the prompt delta. Raw provider logs, runtime
metadata, reasoning fields, private annotations and machine paths are excluded.
The public snapshot includes this projection, not the raw experiment directory

In Propose, **Propose your own change** opens a local editor beside the supplied
example. Choose supporting failure families, a change type, a concrete change,
an optional cause hypothesis and a success check. Experiment settings keep the
primary criterion, regression check and planned repeats explicit

The personal proposal remains **Not run**. Save creates a revision; unfinished
edits are kept separately, so Export uses the saved revision. Reload restores the
draft when browser storage is available; a storage error is shown explicitly.
Start over resets the example and preserves the personal proposal. Clear my draft
removes only that personal draft. Export revisions before replacing them if you
need a history

Export produces a source-bound `custom-proposal-draft-v1` planning document,
including baseline evidence references and an unverified hypothesis. It is not
a CLI `failure-action-plan-v1`: applying the idea requires a separately prepared
experiment and reviewed action plan. Saving or rejecting the supplied example
does not assign its candidate results to the personal proposal. The editor checks
structure, not whether the hypothesis is true or the proposed check is sufficient

Build an authored fixture with the `workflow` runtime, then export only the public assets:

```sh
python examples/docs-answer/run_demo.py --output /tmp/ail-web-fixture
python web_demo/build_demo.py --fixture-run /tmp/ail-web-fixture --output /tmp/ail-web-site
python -m http.server 8782 --bind 127.0.0.1 --directory /tmp/ail-web-site
```

Open `http://127.0.0.1:8782`, run the example, inspect evidence, replay or reject the
supplied candidate, then generate and download unscored synthetic task inputs

The authored guided page embeds its data in a separate script. The new main story
and Results fetch same-origin JSON and need static HTTP serving. No external fonts,
provider calls, analytics or backend dependency are used

Citation linkage and comparison arithmetic run in the browser. Semantic criteria,
reference labels and candidate answers are authored beforehand. Identical fixture
repeats, correlated families, missing heldout validation and shared reference origin
are explicit. A citation does not establish claim truth or version relevance

Selection state stays in browser local storage as a demo checkpoint. Stage, case,
version and copy resume on reload; generated inputs are recreated explicitly.
It is never exported as human gold or sent to a service. Start over returns the
example to its initial state while preserving the separate personal proposal

The interface uses native buttons, dialogs and disclosures, with focus restored
after stage/case changes. Light/dark colors and responsive layouts share the same
spacing and type hierarchy. Fonts are local fallbacks; no external assets load

The exporter uses an explicit field allowlist and rejects machine paths and real
annotation mode. Private databases, source metadata and workflow controls are absent

