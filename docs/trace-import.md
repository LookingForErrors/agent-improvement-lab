# Trace and task import

This deterministic layer normalizes recorded data. It does not run an assistant, evaluate agent quality, turn references into human gold, or connect to Langfuse.

## Contracts

Task: stable ID, user objective, optional family/split/language, context and metadata.
Trace: stable ID, optional task ID/embedded task, family/split/language/provenance/version, ordered messages and metadata.
Assessment: trace ID, verdict, assessor kind/identity, rubric version, error types and evidence step IDs. `validate_against(trace)` checks the trace ID and evidence references. Import never creates assessments. Human, LLM, deterministic and generator labels remain separate.

The Python contracts live in [schemas.py](../agent_improvement_lab/schemas.py). Their version is the string `1`. Unknown normalized fields are rejected; raw source fields are retained in `source.record`. Source also records the filename, record index and JSONL line. Normalized optional fields do not replace raw source evidence.

Messages retain list order. Existing step IDs survive; a missing step ID receives `s0000`, `s0001`, etc. Generated IDs are trace-local; a collision with an existing ID is an error. Roles: system, developer, user, assistant, tool. Content and optional reasoning retain JSON values. Reasoning is not inferred or requested. Tool-call messages may have empty content; other messages require a mapped content field. Explicit null is preserved and is distinct from a missing field.

Tool arguments may be a JSON object or a JSON string encoding an object. The original encoding remains in the source record. The importer does not check business argument requirements against tool definitions: an empty object is valid imported data and can be an actual agent mistake.

## Mapping an export

[vendor-example.yaml](../mappings/vendor-example.yaml) imports the shipped synthetic vendor fixtures with nested paths, custom roles, optional reasoning and embedded task details. [docs-answer.yaml](../mappings/docs-answer.yaml) imports the generated NovaDocs example. The versioned Task, Trace and Assessment definitions are in the shipped Python schema module linked above.

```yaml
mapping_version: '1'
kind: trace
fields:
  id: trace.uuid
  task_id: request.id
  language: locale
messages:
  path: events
  fields:
    role: actor
    content: body
  role_map:
    customer: user
    bot: assistant
```

Mapping is a finite declarative config: fields, defaults, optional records_path, messages and embedded task. Paths traverse object keys using dots; no array indices, expressions, templates, network references or executable transformations. Missing optional paths are omitted; defaults apply only to missing fields, not explicit null/false/zero. Unknown config fields, duplicate YAML keys and unsafe YAML tags are rejected. Add `messages.tool_calls` fields id/name/arguments and message tool_call_id/function mappings for exports containing tools.

JSON accepts a single object, an array, or a wrapper array selected by `records_path: export.traces`. JSONL is one object per nonblank line; records_path is unsupported for JSONL. Duplicate JSON object keys and non-finite numbers are rejected instead of silently discarding or changing evidence.

## Structural validity and incomplete traces

Reject duplicate trace IDs within a file, duplicate step/call IDs, tool results without an earlier matching call, repeated results and mismatched result names. Missing essential fields include a record/line and field diagnostic. A tool call with no recorded result is retained with `unanswered_tool_call` warning: this may be a truncated capture or a failed execution. It is not an automatic agent failure. Missing task details are allowed on a trace; later evaluation must join the task or use unassessable when essential context is absent.

Parallel branches and multiple calls in one assistant message are outside this sequential MVP and produce an explicit structural error. No order is inferred from timestamps or reconstructed from unordered spans. Empty inputs are errors.

## Run

From the repository root, use the environment created in [Quickstart](quickstart.md):

```sh
python -m agent_improvement_lab.import_traces \
  tests/fixtures/import/vendor.jsonl \
  --mapping mappings/vendor-example.yaml \
  --output /tmp/ail-vendor-normalized.jsonl \
  --diagnostics /tmp/ail-vendor-import-report.json
```

There is no implicit overwrite. Output and diagnostics must differ from the input and mapping. `--overwrite` explicitly permits replacing output files. The CLI validates the entire input before writing normalized output. Any import error exits 2 and leaves existing normalized output unchanged; diagnostics may still be written. A successful output uses atomic file replacement. The API `import_file()` exposes accepted records and diagnostics even in a mixed invalid batch; callers must check `result.ok` before treating the batch as accepted. Neither interface produces an agent verdict.

JSONL parsing reads one line at a time, but normalized records remain in memory until the batch is accepted. JSON loads the full document. This is not an unbounded production stream importer; larger-scale state persistence and limits are future work.

## Blind annotation boundary

Normalized traces are internal data, not a blind annotation export. `source.record`, metadata, agent version, provenance and embedded context may contain reference/judge/version fields. Never serve them directly to an annotator. The [annotation interface](annotation.md) constructs an allowlist export, while separately retaining original source evidence for audit. Optional reasoning may be sensitive and needs an explicit display policy there.

## Reproduce checks

```sh
python -m unittest discover -s tests -p test_import.py
```

The shipped tests include two external file encodings, raw field preservation, corrupt tool links, strict mappings, missing versus null data, incomplete calls, atomic failed-batch behavior and independent assessment evidence references. They do not measure judge or assistant quality.
