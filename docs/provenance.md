# Provenance and release boundary

Project source, rules, examples and fixture assessments were written from scratch.
Public examples contain synthetic data and no private source material.

NovaDocs and the routing product are fictional. Their tools and user facts are
controlled mocks. The guided documentation example uses authored trajectories,
assessments and repairs; it makes zero model calls and contains no human gold.
Fixture corrections are not measured product improvements.

The current routing replay contains 370 recorded model trajectories and 134
authored successful trajectories across 252 pairs in 84 families. Shared repairs
raise both versions for presentation. Its 58.7% → 86.5% rates are descriptive.
Eleven disputed records received two fresh blind AI reviews; six pairs remain
Unknown. These AI assessments and authored successes are not human gold.

Public files are selected by explicit allowlists. Private annotation databases,
joins, historical raw experiments, environments, machine configuration and
portfolio controls stay outside the release snapshot. Saved public projections
retain source bindings; building or staging them makes no model calls or publication.

Dependencies include Pydantic, PyYAML, LangGraph/SQLite and Inspect AI/psutil.
Their code is installed from exact version locks, not vendored. Upstream package
metadata and licenses remain the authority for each dependency.

Project source and bundled documentation are licensed under [MIT](../LICENSE).
Third-party components retain their own licenses.
