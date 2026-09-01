"""Pre-flight smoke gate for Genius.

Runs the solver on a small set of versioned adversarial cases under
`genius/smoke_cases/` before the real evaluation loop. Any case that
trips Genius's own `evaluate_case_solution` validation (cross-row
courier dup, cross-row task dup, malformed rows, out-of-table rows)
fails the whole submission — `run_judge` raises `SmokeFailed` and
the per-case loop never runs.

The point is to surface structural bugs (especially the local-search
stale-snapshot pattern that produces cross-row courier dup) on a
fixed, reproducible input set, instead of waiting for the live data
to randomly trigger them. Adversarial cases are constructed so that
a single shared Pareto-frontier courier across multiple bundles
provokes naive multi-anchor / swap-chain solvers into reusing the
same courier in two output rows.

Reuses the existing detector in `official_like.evaluate_case_solution`
— there is no second source of truth for "what counts as invalid".
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Online judge sandbox has been observed to reject solvers that import the
# `typing` module — symptom is 10/10 all-error with case-uniform penalty
# scores. The contract signature is therefore the bare form below; generic
# annotations like `List[Tuple[str, list]]` are not allowed.
PROHIBITED_IMPORTS = frozenset({"typing"})
REQUIRED_SOLVE_SIGNATURE = "def solve(input_text: str) -> list:"
# Online wall budget protocol: solver declares the literal exactly, Genius
# rewrites it locally. See genius/solver_executor.materialize_local_solver.
REQUIRED_BUDGET_SEC_LITERAL = "BUDGET_SEC = 10.0"


SKIP_ENV_VAR = "GENIUS_SKIP_SMOKE"

from genius.official_like import evaluate_case_solution
from genius.solver_executor import (
    DEFAULT_CASE_TIMEOUT_SEC,
    DEFAULT_PYTHON_CMD,
    execute_solver_case,
    materialize_local_solver,
)


SMOKE_CASES_DIR = Path(__file__).resolve().parent / "smoke_cases"


@dataclass
class SmokeFailure:
    case_name: str
    message: str
    validation_errors: list[str] = field(default_factory=list)
    exec_error: str = ""

    def render(self) -> str:
        lines = [f"case={self.case_name} msg={self.message!r}"]
        if self.exec_error:
            lines.append(f"  exec_error: {self.exec_error}")
        for err in self.validation_errors[:10]:
            lines.append(f"  - {err}")
        more = len(self.validation_errors) - 10
        if more > 0:
            lines.append(f"  ... (+{more} more)")
        return "\n".join(lines)


@dataclass
class SmokeResult:
    passed: bool
    cases_run: int
    failures: list[SmokeFailure] = field(default_factory=list)
    # Tmp file Genius wrote with `BUDGET_SEC` rewritten to the local wall
    # budget. Owned by the caller (run_judge cleans it up in finally).
    local_solver_path: Path | None = None

    def render(self) -> str:
        if self.passed:
            return f"smoke PASS ({self.cases_run} adversarial case(s))"
        head = f"smoke FAIL ({len(self.failures)}/{self.cases_run} adversarial case(s) failed)"
        return head + "\n" + "\n".join(f.render() for f in self.failures)


class SmokeFailed(RuntimeError):
    """Raised by run_judge when the smoke gate rejects the solver.

    The caller (Fool, frontend) is expected to treat this as a
    catastrophic outcome — no replacement of incumbent, write to
    try_error memory, surface validation_errors to the LLM so the
    next round can fix the structural bug.
    """

    def __init__(self, result: SmokeResult) -> None:
        super().__init__(result.render())
        self.result = result


def _check_solver_contract(solver_path: str) -> list[str]:
    """Static gate: enforce the online-judge entrypoint contract.

    Returns a list of human-readable errors; empty means pass. Checks:
      1. Top-level `def solve(input_text: str) -> list:` exists.
      2. The return annotation is the bare name `list` (or absent).
         Subscripted forms like `List[Tuple[str, list]]` are rejected.
      3. No `import typing` or `from typing import ...` anywhere in the
         module — the online sandbox has been seen to reject these even
         though `typing` is stdlib.
    """
    errors: list[str] = []
    try:
        source = Path(solver_path).read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read solver source: {exc}"]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error in solver: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in PROHIBITED_IMPORTS:
                    errors.append(
                        f"prohibited import at line {node.lineno}: `import {alias.name}`"
                        " — online sandbox may reject the typing module"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in PROHIBITED_IMPORTS:
                names = ", ".join(a.name for a in node.names)
                errors.append(
                    f"prohibited import at line {node.lineno}: "
                    f"`from {node.module} import {names}` — drop the typing import"
                )

    solve_fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "solve"),
        None,
    )
    if solve_fn is None:
        errors.append(
            f"missing top-level `{REQUIRED_SOLVE_SIGNATURE}` — entrypoint contract"
        )
        return errors

    ret = solve_fn.returns
    if ret is None or (isinstance(ret, ast.Name) and ret.id == "list"):
        pass
    else:
        try:
            rendered = ast.unparse(ret)
        except AttributeError:
            rendered = "<complex annotation>"
        errors.append(
            f"solve return annotation must be bare `list`, got `{rendered}` "
            f"(required: `{REQUIRED_SOLVE_SIGNATURE}`)"
        )

    errors.extend(_check_budget_sec_protocol(tree, source))
    return errors


def _check_budget_sec_protocol(tree: ast.AST, source: str) -> list[str]:
    """Wall-budget protocol: solver must declare exactly one module-level
    `BUDGET_SEC = 10.0` and must call `time.monotonic()` or `time.time()`
    at least once. Genius rewrites the literal locally; without it the
    rewrite would silently no-op and the solver would self-limit at the
    online 10s wall on a 2.5×-slower local machine — guaranteed timeouts.
    The time-call check is a weak guard against forgetting the deadline
    entirely; it does not prove the deadline is honored.
    """
    out: list[str] = []
    budget_targets = 0
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == "BUDGET_SEC":
                budget_targets += 1
                val = node.value
                is_literal_10 = (
                    isinstance(val, ast.Constant)
                    and isinstance(val.value, float)
                    and val.value == 10.0
                )
                if not is_literal_10:
                    try:
                        rendered = ast.unparse(val)
                    except AttributeError:
                        rendered = "<expr>"
                    out.append(
                        f"BUDGET_SEC at line {node.lineno} must be the bare literal `10.0`, "
                        f"got `{rendered}` — Genius rewrites this line locally; the value "
                        "is the online wall ceiling and must NOT be changed by the solver"
                    )
    if budget_targets == 0:
        out.append(
            f"missing module-level `{REQUIRED_BUDGET_SEC_LITERAL}` — "
            "required protocol constant for wall budget"
        )
    elif budget_targets > 1:
        out.append(
            f"{budget_targets} module-level BUDGET_SEC assignments found; expected exactly 1"
        )

    has_time_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "time"
                and node.func.attr in {"monotonic", "time", "perf_counter"}
            ):
                has_time_call = True
                break
    if not has_time_call:
        out.append(
            "no `time.monotonic()` / `time.time()` / `time.perf_counter()` call found — "
            "solver must compute a deadline against BUDGET_SEC and self-limit before 9.5s"
        )
    return out


def run_smoke(
    solver_path: str,
    *,
    python_cmd: str = DEFAULT_PYTHON_CMD,
    max_case_seconds: float = DEFAULT_CASE_TIMEOUT_SEC,
    cases_dir: Path | str = SMOKE_CASES_DIR,
    local_budget_sec: float | None = None,
) -> SmokeResult:
    """Contract gate + adversarial-case exec.

    The contract check runs on `solver_path` (the original — that's where
    `BUDGET_SEC = 10.0` must be the bare literal). After it passes, smoke
    materializes a tmp copy with `BUDGET_SEC` rewritten to `local_budget_sec`
    (defaults to `max_case_seconds`) and executes adversarial cases against
    that copy. The tmp path is returned on `SmokeResult.local_solver_path`
    so `run_judge` can reuse it for the real case loop and unlink it at end.
    """
    # Env-var escape hatch for test fixtures / contract tests that submit
    # trivially hardcoded solvers via subprocess. Production callers
    # (Fool, frontend) must never set this.
    if os.environ.get(SKIP_ENV_VAR, "").strip() in {"1", "true", "yes"}:
        return SmokeResult(passed=True, cases_run=0)

    contract_errors = _check_solver_contract(solver_path)
    if contract_errors:
        return SmokeResult(
            passed=False,
            cases_run=0,
            failures=[
                SmokeFailure(
                    case_name="__contract__",
                    message="entrypoint contract violation",
                    validation_errors=contract_errors,
                )
            ],
        )

    # Contract passed — materialize before any exec so the rewritten copy
    # is available to both smoke cases and (via SmokeResult) the real case
    # loop. Returned even when no smoke cases exist; that path still runs
    # solvers later in run_judge.
    budget = float(local_budget_sec) if local_budget_sec is not None else float(max_case_seconds)
    local_path = materialize_local_solver(solver_path, budget)

    case_dir = Path(cases_dir)
    if not case_dir.is_dir():
        return SmokeResult(passed=True, cases_run=0, local_solver_path=local_path)

    case_files = sorted(p for p in case_dir.glob("*.txt") if p.is_file())
    if not case_files:
        return SmokeResult(passed=True, cases_run=0, local_solver_path=local_path)

    exec_path = local_path
    failures: list[SmokeFailure] = []
    for case_file in case_files:
        text = case_file.read_text(encoding="utf-8")
        exec_result: dict[str, Any] = execute_solver_case(
            python_cmd=python_cmd,
            solver_path=exec_path,
            case_path=case_file,
            timeout_sec=max_case_seconds,
        )

        if not exec_result.get("ok", False):
            err_type = str(exec_result.get("error_type", "unknown"))
            err_msg = str(exec_result.get("error", "execution error"))
            # python missing/incompatible is a config error, not a smoke fail —
            # let the caller re-raise it as RuntimeError downstream.
            if err_type in {"python_missing", "python_incompatible"}:
                raise RuntimeError(f"smoke aborted: {err_type}: {err_msg}")
            failures.append(
                SmokeFailure(
                    case_name=case_file.stem,
                    message=f"exec_failed:{err_type}",
                    exec_error=err_msg,
                )
            )
            continue

        raw_output = exec_result.get("output", [])
        ev = evaluate_case_solution(text, raw_output)
        if ev.get("illegal_solution") or int(ev.get("invalid_rows", 0)) > 0:
            failures.append(
                SmokeFailure(
                    case_name=case_file.stem,
                    message=str(ev.get("message", "invalid")),
                    validation_errors=list(ev.get("validation_errors", []) or []),
                )
            )

    return SmokeResult(
        passed=(not failures),
        cases_run=len(case_files),
        failures=failures,
        local_solver_path=local_path,
    )
