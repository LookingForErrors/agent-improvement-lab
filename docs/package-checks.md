# Package checks

Maintainer instructions for verifying a release. These steps are separate from
the [first-run example](quickstart.md). Use fresh output directories

## Install the portable snapshot

Python 3.12 is the current supported version. The `workflow` extra supplies
LangGraph/SQLite replay; `execution` supplies Inspect AI and resource supervision.
Core import, annotation, criteria and judge metrics use Pydantic and PyYAML

The snapshot contains an explicit public-file allowlist. It excludes historical
experiments, real annotation databases, project controls and machine-specific
model loading. Creating it does not publish anything

```sh
python3.12 scripts/stage_offline.py --output /tmp/ail-install-source
python3.12 -m venv .venv-release-check
ail_python="$PWD/.venv-release-check/bin/python"
"$ail_python" -m pip install --only-binary=:all: -r /tmp/ail-install-source/requirements/build.lock.txt
"$ail_python" -m pip install --only-binary=:all: -r /tmp/ail-install-source/requirements/combined.lock.txt
"$ail_python" -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/ail-wheel /tmp/ail-install-source
"$ail_python" /tmp/ail-install-source/scripts/verify_installed.py --snapshot /tmp/ail-install-source --wheel-directory /tmp/ail-wheel --inventory-only --output /tmp/ail-wheel-check
"$ail_python" -m pip install --no-deps /tmp/ail-wheel/agent_improvement_lab-0.1.0-py3-none-any.whl
. .venv-release-check/bin/activate
mkdir /tmp/ail-outside
cd /tmp/ail-outside
"$ail_python" /tmp/ail-install-source/scripts/verify_installed.py --snapshot /tmp/ail-install-source --wheel-directory /tmp/ail-wheel --output /tmp/ail-installed-check
```

Run with Python path overrides unset. Every output folder must be new. The helper
binds all installed package bytes to the snapshot, checks six commands and UI
resources outside source, then exercises graph replay, rejection, crash/resume,
an empty human checkpoint and both execution fixtures, including one-time
recovery. Open `/tmp/ail-installed-check/installed-check.json`

For replay alone, use `requirements/workflow.lock.txt` and `--mode workflow`.
For execution alone, use `requirements/execution.lock.txt` and `--mode execution`.
These are exact version closures, not hash-locked dependency downloads

## What has been verified

The earlier clean-install check on macOS/Python 3.12.14 passed 194 tests, six commands and
nine installed checks. JSON/JSONL import, a custom criterion, its paired comparison
and the site build were verified. All 24 installed package files matched the snapshot

The current interface passed 53 focused checks in source and the staged copy;
core code is unchanged. The MIT metadata revision has source checks; a new wheel
build/install has not yet been repeated. Hosted CI and other platforms remain unverified

