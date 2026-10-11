# Agent Improvement Lab

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

[Open the interactive demo](https://lookingforerrors.github.io/agent-improvement-lab/)

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

