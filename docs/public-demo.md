# Browser demo

Serve the built `demo` directory over localhost or static hosting. The site needs
no model endpoint. [Build instructions](../web_demo/README.md)

## Improvement loop

The homepage follows five scenes:

1. **Before** — the task roster and routing outcomes
2. **Find** — three recorded routing failures and two correct routes worth preserving
3. **Change** — the exact prompt excerpt and added tool-state guard
4. **Verify** — the same five tasks before/after, with three fixes and two preserved routes
5. **After** — the complete roster, gains, regressions and unresolved pairs

Play/Pause, manual scene selection, replay and reduced motion share the same data.
The fixed shuffled order keeps each task in the same tile before and after

Find and Verify share the selected example. Red marks identify the assistant's
error; user replies and ticket details remain neutral. Green marks identify the
correct routing action. Extra questions remain visible; Results shows each criterion

The current presentation dataset shows **58.7% → 86.5%** on 252 paired tasks in
84 families. This synthetic replay combines recorded and authored trajectories;
the rates illustrate the workflow. [Data sources and measurement details](provenance.md)

**Results** exposes every pair, six criteria, filters, source identities and JSON
exports. Evidence links open and focus original messages or tool actions. Missing
or invalid evidence cannot invent a step or change a score. The viewer consumes
saved evaluation results; highlighting does not participate in batch evaluation

**Evidence & limits** and JSON exports retain source details and evaluation limits

## Trace catalog

The catalog contains 30 working scenarios from 30 families and 180 scripted
trajectories. Search includes dialogue and full tool context. Scenarios include
successful clarification, unresolved ambiguity after two questions, corrections,
topic changes and longer histories. Composition counts are authored examples,
not measured failure frequencies

Conversation cards are primary; tool calls and linked results appear as compact
expandable actions. Error evidence remains visible while arguments and full tool
responses stay available in details. Export preserves the complete original trace

## Guided example and earlier pilot

`guided.html` runs the authored NovaDocs example: inspect → diagnose → propose →
compare → generate new test inputs. Citation linkage and arithmetic run in-browser;
semantic assessments, references and candidate answers are supplied fixtures.
The generated inputs are unscored proposals

A visitor may reject the supplied change or write an alternative with evidence,
a cause hypothesis and a verification plan. Local draft/checkpoint persistence
and JSON export do not apply the proposal or run an assistant. Start over preserves
personal drafts; clearing a draft is a separate action

`pilot.html` retains an earlier saved comparison and its rejected candidate

## Source workflow

The Python workflow supplies durable blind annotation, criteria, judge reports,
action drafts, approval/rejection checkpoints and paired replay or bounded execution.
[Quickstart](quickstart.md) and [experiment](experiment.md) explain how to run it

Building and viewing the site makes no model calls and uses no private annotation exports
