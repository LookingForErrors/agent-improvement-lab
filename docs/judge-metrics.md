# Prepare human / judge comparison

This deterministic module compares frozen decisions and writes analyst-only
JSON and Markdown reports. It performs no inference, judges no trace itself,
and changes no annotation workspace. Real judge validation requires an independent
reference package and a versioned judge run.

## Run the supplied example

```sh
python examples/docs-answer/run_demo.py --output /tmp/ail-metrics-source
```

Read `/tmp/ail-metrics-source/workflow/judge-validation.md` and its JSON report.
The eight baseline decisions are authored reference/judge fixtures with four
families and two identical copies per family. Perfect agreement is an arithmetic
example; their shared origin does not validate an independent judge

For the standalone metrics interface, use another fresh output:

```sh
python -m agent_improvement_lab.judge_metrics prepare \
  --traces /tmp/ail-metrics-source/inputs/baseline-normalized.jsonl \
  --human /tmp/ail-metrics-source/inputs/fixture-annotations.json \
  --judge /tmp/ail-metrics-source/inputs/baseline-judge.json \
  --rubric /tmp/ail-metrics-source/inputs/rubric.txt \
  --dataset-version docs-answer-fixture-v2 --data-kind test_fixture \
  --output /tmp/ail-metrics-input.json
python -m agent_improvement_lab.judge_metrics compare \
  --input /tmp/ail-metrics-input.json --output-dir /tmp/ail-metrics-report \
  --resamples 2000 --seed 17
```

These fixtures contain no unassessable labels; the module retains that class and
null denominators. Contract tests cover missing labels, abstention, all verdict
pairs and clustered resampling:

```sh
python -m unittest discover -s tests -p test_judge_metrics.py
```

## Input and identity

The engine requires a **complete declared trace inventory**, not just labeled
traces. Missing entries are part of the denominator. The private annotation
export contains latest saved labels only, so it cannot supply that population.

A prepared `judge-validation-input-v1` bundle fixes dataset and agent versions,
rubric SHA-256, judge version/model/run/prompt/config identities, source hashes,
task/family/split, length/noise metadata and nullable human/judge decisions.
A human decision has its assessor name, latest revision and original evidence
steps; scenario evidence/comment, error types and timestamp are preserved.
The report-wide label inventory covers agreeing and unpaired decisions as well;
revision history is not additional samples. Human names and data_kind are
self-declared provenance, not proof of independent adjudication or label truth.
`test_fixture` must be used for mocks/QA; `human_validation` declares actual
human-reference comparison, not automatic acceptance as gold.

For real data later, export a private annotation-file snapshot using the
annotation CLI; never point this module at the live SQLite DB. Prepare the
selected full inventory against that snapshot and one judge run:

```sh
python -m agent_improvement_lab.judge_metrics prepare \
  --traces normalized-traces.jsonl \
  --human human-labels.private.json \
  --judge judge-labels.json \
  --rubric your-rubric.txt \
  --dataset-version dataset-v1 \
  --data-kind human_validation \
  --output comparison-input.json
```

This is a future usage example: real judge predictions have not been generated.
The runnable adapter example above uses the generated NovaDocs fixture inputs. Judge file format:

```json
{
  "format": "judge-assessments-v1",
  "judge_identity": {
    "version": "judge-v1",
    "model": "explicit-model-id",
    "run_id": "one-selected-run",
    "prompt_sha256": "64 lowercase hex characters",
    "config_sha256": "64 lowercase hex characters"
  },
  "assessments": []
}
```

Each assessment follows the existing Assessment schema (`assessor_kind: llm`),
uses the rubric SHA-256 as `rubric_version`, and includes `source_sha256` and an
exact copy of `judge_identity` in metadata. Evidence steps must exist. Unknown or
duplicate labels, changed source/rubric, conflicting task/family/split, mixed
agent versions and changed judge model/prompt/config/run are rejected before
comparison. Source hashes use the same canonical normalized Trace dump as
annotation, including defaults; they are not raw JSONL file-byte hashes.
The prepare command checks original source linkage. The compare command trusts
a frozen prepared bundle; hand editing it changes its input hash but cannot
independently prove its declarations against the original files.

## Metric definitions

The fixed verdict order is correct / incorrect / unassessable. Confusion rows
are human reference, columns judge, following the conventional orientation in
[scikit-learn confusion matrix documentation](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.confusion_matrix.html).
All paired decisions enter this three-class matrix, including unassessable.

For each class: precision = TP/(TP+FP), recall = TP/(TP+FN), F1 =
2TP/(2TP+FP+FN), as described in the [scikit-learn F1 documentation](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.f1_score.html).
Each zero denominator independently yields null, never a fabricated zero or
perfect score. F1 may be zero even when precision or recall is undefined.
Reports keep class support, predicted count and TP/FP/FN. No macro score hides
missing classes; metrics here use ordinary code, with no scikit-learn install.

Paired exact agreement uses every pair. A separate secondary agreement is
conditional on both sides being assessable; it must not replace the primary
result, since abstention could hide errors. Equal-family agreement averages
family-specific agreement and names a different estimand from trace-weighted
agreement. Repeated traces increase row count, not the number of unique tasks
or families. No agreement is called objective accuracy or causal improvement.

Coverage distinguishes absent labels from explicit unassessable outcomes.
Counts/rates show expected inventory, human/judge present and missing, paired,
both-assessable, unassessable among each side's present labels and scenario flags.
Scenario flags do not silently exclude otherwise labeled traces.

## Slices and uncertainty

Slice definitions version 1 are fixed before labels. Length measures Unicode
characters in JSON-serialized content and reasoning, plus canonical tool
arguments, excluding system/developer messages. It includes JSON quotes/null,
is not a tokenizer measure, and uses <2,000 / 2,000..15,999 / >=16,000 bins.
Noise is explicit trace metadata `noise: clean|noisy|unknown`; missing becomes
unknown, no noise is inferred from verdicts. Family and working/heldout slices
are separate; empty declared bins remain. Pooled overall working/heldout results
are descriptive only and cannot serve as held-out validation.

Bootstrap draws scenario families with replacement, retaining all paired traces
within each draw and multiplicity when a family is drawn more than once. This
follows the cluster resampling principle in [Stata's bootstrap guidance](https://www.stata.com/support/faqs/statistics/bootstrap-with-panel-data/).
The [SciPy bootstrap documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)
describes percentile intervals. This module uses a fixed seed and linear
interpolated 2.5th/97.5th percentiles; it does not install SciPy or claim BCa.

Intervals are for trace-weighted paired agreement and per-class metrics,
conditional on the observed paired families. Missing labels are not imputed,
and may introduce selection bias. Each metric records defined and undefined
replicate counts. If undefined draws occur, its interval uses only defined
replicates and is explicitly conditional on them, rather than an unconditional
95% interval. Fewer than two paired families suppress all intervals. A degenerate
interval is flagged and does not establish certainty. Few families and small
samples remain weak evidence even with many resamples. Slice metrics are point
summaries; slice intervals and version-delta inference are not implemented here.

## Next stages

Do not tune a judge using held-out labels. First collect/review human reference,
then run a versioned judge and compare on working data. Reserve a separate
family-heldout check after tuning. Thresholds, deployment, error taxonomy
alignment, cross-version regressions, SFT/RL and product changes require their
respective next tasks. Disagreements list observed label differences and evidence;
a root cause or product intervention is not inferred from these metrics alone.

Reports contain judge outputs and private linkage. They are analyst artifacts,
never part of the blind annotator API/UI/export. Keep human snapshots private and
preserve them on rollback.
