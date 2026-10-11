# Local blind trace review

Start an empty queue using the supplied fictional documentation assistant:

```sh
python examples/docs-answer/run_demo.py --human --output /tmp/ail-human-demo
python -m agent_improvement_lab.annotation serve \
  --db /tmp/ail-human-demo/workflow/annotations.sqlite3 --port 8780
```

Open **http://127.0.0.1:8780**. The eight baseline trajectories are authored
fixtures; no real services are changed and no labels are prefilled. Versions,
reference decisions and judge verdicts are hidden. For real input, use your own
normalized sequential traces and versioned rubric

## Annotate

1. Open **Правила оценки / Evaluation rules** and read the common product rubric.
2. Read the trace chronologically. Initial authored conversation is context.
   The green divider marks the start of the evaluated assistant continuation.
   Later authored user replies are explicitly marked as scenario replies.
3. Assistant text, tool calls with arguments and tool results have separate
   cards. Expand recorded tool history when present, search it, or jump to its current record.
   Original safe step data and recorded reasoning are available when present.
4. Select Correct, Incorrect, or Cannot assess. For Incorrect, select at least
   one evaluated assistant step as evidence, then error types and an explanation.
   Add user messages or tool results as supporting evidence when helpful. Initial user requests and later scenario replies are both available; user evidence alone cannot identify an assistant error. Cannot assess requires an explanation.
5. Flag scenario/input problems separately: a defective authored reply is not
   automatically an error of the evaluated model. Select scenario evidence and
   explain it. Use Cannot assess when it prevents a reliable verdict.
6. Press **Сохранить / Save** or **Сохранить и дальше / Save and next**.
   The next trace opens only after a committed save. Errors appear beside the
   save buttons. Cmd/Ctrl+Enter saves and advances. On narrow windows the
   floating annotation button jumps to the form.

Russian/English UI switch does not translate source trace text or the rubric.
Queue numbers and order are stable for this workspace, randomized once at
creation; numbers are not raw trace IDs. If the source includes a trusted current
ticket instruction, its ID is visible to support tool-argument assessment.
Other sequential domains need no ticket instruction.

## Durable labels and drafts

Saved decisions live in the selected SQLite workspace, with immutable
revision history. Browser-local drafts survive reload in the same browser
profile and origin; drafts are not saved labels. A server restart preserves
saved decisions. Keep the same port to retain browser drafts. Name is an
assessor declaration, not authenticated identity. This MVP is a single-user
local tool, not a multi-user labeling platform.

Two tabs cannot silently overwrite one another: a stale revision produces a
conflict, preserves the draft and offers to load the saved decision. If a save
or connection fails, keep the draft and retry. A failed response can be
ambiguous after a successful commit; reload the saved revision before retrying.

## Prepare your own queue

The following filenames are placeholders for your own normalized data and rubric:

```sh
python -m agent_improvement_lab.annotation prepare \
  --db private_data/workspace.sqlite3 \
  --traces your-normalized-traces.jsonl --rubric your-rubric.txt
python -m agent_improvement_lab.annotation serve \
  --db private_data/workspace.sqlite3 --port 8780
```

There are no model calls. Stop the helper with Ctrl+C. Repeating prepare with
unchanged source/rubric preserves IDs, queue and labels. New IDs append. Changed
contents, rubric or blind projection reject refresh atomically; use a separate
workspace for another experiment. Never delete saved labels as a rollback step

The underlying importer supports JSON/JSONL and YAML mappings (see
[trace import](trace-import.md)). The viewer requires explicit
`metadata.message_sources` for each step (`input` for authored context/replies,
`generate` or null for evaluated output). Unsupported text content or ambiguous
origin fails closed. A trusted `Current ticket: ID` instruction is optional;
more than one is rejected. The [documentation example](docs-answer-demo.md) uses
the same viewer without a ticket. Parallel spans and rich content remain unsupported.

## Export and backup

The UI exports only the blind public projection and latest saved decisions.
Source/model/run/reference/judge metadata and original step IDs stay private.
The CLI can join labels to original trace/task/family/split and evidence IDs:

```sh
python -m agent_improvement_lab.annotation export-private \
  --db /tmp/ail-human-demo/workflow/annotations.sqlite3 \
  --output private_data/human-labels.private.json
```

Do not serve or publish the private database/export. Keep private inputs and exports outside a public snapshot. Private linkage supports clustered validation and
experiment comparison, rather than exposing it to an annotator beforehand.
Blindness protects the UI/API/export boundary, not someone with direct access
to private local source files. Use SQLite backup rather than copying a live
WAL database alone:

```sh
python - <<'PYBACKUP'
import sqlite3
with sqlite3.connect('/tmp/ail-human-demo/workflow/annotations.sqlite3') as source:
    with sqlite3.connect('/tmp/ail-human-demo/workspace.backup.sqlite3') as target:
        source.backup(target)
PYBACKUP
```

The loopback helper binds only 127.0.0.1, serves fixed asset/API routes, and checks
Host/origin/mutation token. It does not expose project directories. No cloud,
auth service, writeback, judge inference or training is part of this slice.
QA annotations are isolated under task artifacts and are never human gold.

## Appearance

Use the Dark mode / Тёмная тема toggle in the top bar. The initial theme follows
the operating system until you choose explicitly; the choice is remembered in
this browser. Switching theme does not modify annotation drafts or saved labels.
