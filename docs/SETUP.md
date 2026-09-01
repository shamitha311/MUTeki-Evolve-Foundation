# Setup

## Prerequisites

- Python 3.10+ for the application contracts and tests.
- `uv` or another Python environment manager.
- For upstream Muteki verification: Python 3.13-compatible dependencies and,
  for live workers, the upstream runtime prerequisites and authorized disposable
  sandbox.
- For upstream MTASA Genius subprocess verification: Python 3.6 in addition to
  the modern Python runtime, plus MTASA's documented dependencies.

## Application contracts and tests

From the project root:

```bash
uv sync --extra dev
uv run pytest
```

The contract tests and deterministic mock scenario do not require LLM
credentials, Docker, network targets, or upstream packages.

## Deterministic mock scenario

```bash
uv run python -c "import asyncio; from orchestration.mock_scenario import run_three_round_scenario; print(asyncio.run(run_three_round_scenario()))"
```

## Muteki source inspection and safe mock verification

The checked-out upstream source is under `vendor/muteki`. The verified
credential-free command is:

```bash
uv run --project vendor/muteki python -m examples.mock_solver
```

This exercises Muteki's in-process deterministic mock, event bus, persistence,
replay, and completion event. It does not claim that a live worker or container
run was verified.

For a real local command deck, upstream documents:

```bash
cd vendor/muteki
./run.sh web --backend-only
```

This was not documented as a successful live verification here because this
environment has no LLM credential and Docker-backed worker execution is
disabled.

## MTASA source inspection and verification

The checked-out upstream source is under `vendor/mtasa`. Its documented setup
requires Python 3.10+ for the main application and Python 3.6 for Genius solver
subprocesses. The required Python 3.6 runtime is absent in this environment, so
the Genius end-to-end command is intentionally not presented as verified.

The upstream documented commands, to run only in a compatible environment, are:

```bash
cd vendor/mtasa
python3 -m pytest genius/tests -q
python3 genius/genius_judge.py \
  --solver fool/templates/solver_minimal.py \
  --input-dir data/sample_10_cases \
  --report /tmp/install_test_report.txt
python3 run_local.py
```

## Upstream source policy

Do not edit files under `vendor/muteki` or `vendor/mtasa`. Application code must
wrap and normalize upstream behavior through `muteki_adapter`; it must not
introduce a second executor, worker manager, Docker system, or host execution
path.
