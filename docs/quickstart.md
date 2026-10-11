# Quickstart

Run these commands from the unpacked release directory.

## Open the case study

```sh
python3 -m http.server 8782 --bind 127.0.0.1 --directory demo
```

Open **http://127.0.0.1:8782**. The site replays saved synthetic examples;
no model, API key or package installation is needed.

## Run the Python example

Requires Python 3.12. Install once in an isolated environment:

```sh
python3.12 -m venv .venv-example
ail_python="$PWD/.venv-example/bin/python"
"$ail_python" -m pip install --only-binary=:all: -r requirements/build.lock.txt -r requirements/workflow.lock.txt
"$ail_python" -m pip install --no-deps --no-build-isolation .
. .venv-example/bin/activate
```

Run the prepared example:

```sh
"$ail_python" examples/docs-answer/run_demo.py --output /tmp/ail-docs-demo
```

Open **`/tmp/ail-docs-demo/RESULT.md`** for the evaluation, proposed change and
comparison. Use a fresh output directory for another run.

The example uses prepared traces and assessments. Add `--human` to start with
an empty annotation queue. [Example details](docs-answer-demo.md).

[Maintainer package checks](package-checks.md) · [Controlled experiments](experiment.md)
