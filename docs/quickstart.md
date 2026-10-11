# Quickstart

## Get the source

```sh
git clone https://github.com/LookingForErrors/agent-improvement-lab.git
cd agent-improvement-lab
```

Or [download the source ZIP](https://github.com/LookingForErrors/agent-improvement-lab/archive/refs/heads/main.zip) and open a terminal in its extracted folder.

## Open the case study

```sh
python3 -m http.server 8782 --bind 127.0.0.1 --directory demo
```

Open **http://127.0.0.1:8782** to inspect traces, a prompt change and the comparison.
No model, API key or package installation is needed.

## Run the Python example

Requires Python 3.12. Install once in an isolated environment:

```sh
python3.12 -m venv .venv-example
. .venv-example/bin/activate
ail_python="$PWD/.venv-example/bin/python"
python -m pip install --only-binary=:all: -r requirements/build.lock.txt -r requirements/workflow.lock.txt
python -m pip install --no-deps --no-build-isolation .
```

Run the prepared example:

```sh
python examples/docs-answer/run_demo.py --output /tmp/ail-docs-demo
```

Open **`/tmp/ail-docs-demo/RESULT.md`** for the evaluation, proposed change and
comparison. Use a fresh output directory for another run.

The example uses prepared traces and assessments. Add `--human` to start with
an empty annotation queue. [Example details](docs-answer-demo.md).

[Package checks](package-checks.md) · [Controlled experiments](experiment.md)
