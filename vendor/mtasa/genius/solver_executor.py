from __future__ import annotations

import ast
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


MAX_SOLVER_BYTES = 100 * 1024
DEFAULT_PYTHON_CMD = "python3.6"
# Online judge wall budget per case. Solvers MUST declare a module-level
# `BUDGET_SEC = 10.0` and self-limit against it; Genius rewrites the literal
# locally (see materialize_local_solver). See CLAUDE.md "Hard runtime constraints".
ONLINE_BUDGET_SEC = 10.0
# Local subprocess wall timeout. Defaults to ~2.5× online to compensate for
# python3.6 + local hardware overhead; configurable via UI.
DEFAULT_CASE_TIMEOUT_SEC = 25.0
_ALLOWED_IMPORTS = {"time", "random", "heapq"}
_ALLOWED_TYPING_NAMES = {"List", "Tuple", "Set", "Dict", "Optional", "Iterable"}

# Matches a single module-level `BUDGET_SEC = 10.0` line. Anchored to line
# start/end so we never replace the constant inside a string or comment.
_BUDGET_SEC_LINE_RE = re.compile(
    r"(?m)^BUDGET_SEC\s*=\s*10\.0\s*(#.*)?$"
)


_RUNNER_CODE = r'''
import json
import runpy
import sys
import time
from pathlib import Path

solver_path = sys.argv[1]
case_path = sys.argv[2]

start = time.perf_counter()

try:
    module = runpy.run_path(solver_path)
    solve = module.get("solve")
    if solve is None or not callable(solve):
        print(json.dumps({"ok": False, "error_type": "missing_solve", "error": "solve callable not found"}))
        raise SystemExit(0)

    input_text = Path(case_path).read_text(encoding="utf-8")
    output = solve(input_text)
    runtime_ms = int((time.perf_counter() - start) * 1000)

    try:
        json.dumps(output)
        serializable_output = output
    except TypeError:
        serializable_output = str(output)

    print(
        json.dumps(
            {
                "ok": True,
                "output": serializable_output,
                "runtime_ms": runtime_ms,
            }
        )
    )
except Exception as exc:
    runtime_ms = int((time.perf_counter() - start) * 1000)
    print(
        json.dumps(
            {
                "ok": False,
                "error_type": "solver_exception",
                "error": f"{type(exc).__name__}: {exc}",
                "runtime_ms": runtime_ms,
            }
        )
    )
'''


def ensure_solver_size_ok(solver_path: str | Path) -> None:
    path = Path(solver_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Solver file not found: {path}")
    size = path.stat().st_size
    if size > MAX_SOLVER_BYTES:
        raise ValueError(
            f"Solver file is {size} bytes, exceeds 100KB limit ({MAX_SOLVER_BYTES} bytes)"
        )
    _ensure_solver_imports_ok(path)


def _ensure_solver_imports_ok(path: Path) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise ValueError(f"Solver is not valid Python syntax: {exc}") from exc

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _ALLOWED_IMPORTS or alias.asname is not None:
                    raise ValueError(
                        "Solver uses unsupported top-level import: "
                        f"import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if (
                node.module == "collections"
                and node.level == 0
                and len(node.names) == 1
                and node.names[0].name == "defaultdict"
                and node.names[0].asname is None
            ):
                continue
            if (
                node.module == "typing"
                and node.level == 0
                and all(
                    alias.name in _ALLOWED_TYPING_NAMES and alias.asname is None
                    for alias in node.names
                )
            ):
                continue
            raise ValueError(
                "Solver uses unsupported top-level import: "
                f"from {'.' * node.level}{node.module or ''} import "
                + ", ".join(alias.name for alias in node.names)
            )


def materialize_local_solver(
    solver_path: str | Path,
    local_budget_sec: float,
) -> Path:
    """Rewrite the solver's `BUDGET_SEC = 10.0` line to the local wall budget
    and return a tmp .py path. The original file is untouched, so what gets
    submitted to the online judge still carries the protocol value 10.0.

    Raises ValueError if the literal is missing or appears more than once.
    Smoke's contract check is the gate that prevents this from firing in
    production — this exception is the last line of defense.
    """
    src = Path(solver_path)
    source = src.read_text(encoding="utf-8")
    matches = _BUDGET_SEC_LINE_RE.findall(source)
    if len(matches) == 0:
        raise ValueError(
            "solver missing required module-level `BUDGET_SEC = 10.0` literal"
        )
    if len(matches) > 1:
        raise ValueError(
            f"solver contains {len(matches)} `BUDGET_SEC = 10.0` lines; expected exactly 1"
        )
    rewritten, _ = _BUDGET_SEC_LINE_RE.subn(
        f"BUDGET_SEC = {float(local_budget_sec)!r}  # local-injected by Genius (online=10.0)",
        source,
        count=1,
    )
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".py", delete=False
    )
    try:
        tmp.write(rewritten)
    finally:
        tmp.close()
    return Path(tmp.name)


def execute_solver_case(
    python_cmd: str,
    solver_path: str | Path,
    case_path: str | Path,
    timeout_sec: float,
) -> dict[str, Any]:
    cmd = [python_cmd, "-c", _RUNNER_CODE, str(solver_path), str(case_path)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error_type": "python_missing",
            "error": f"Python interpreter not found: {python_cmd}",
            "runtime_ms": 0,
            "stderr": str(exc),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_type": "timeout",
            "error": f"Single case runtime exceeded {timeout_sec:g}s",
            "runtime_ms": int(timeout_sec * 1000),
            "stderr": "",
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        if "SyntaxError" in stderr:
            return {
                "ok": False,
                "error_type": "python_incompatible",
                "error": "Solver is not Python 3.6 compatible",
                "runtime_ms": 0,
                "stderr": stderr,
            }
        return {
            "ok": False,
            "error_type": "runtime_boot_error",
            "error": "Solver failed during python3.6 execution",
            "runtime_ms": 0,
            "stderr": stderr,
        }

    if not stdout:
        return {
            "ok": False,
            "error_type": "empty_runner_output",
            "error": "Runner returned empty output",
            "runtime_ms": 0,
            "stderr": stderr,
        }

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error_type": "runner_output_parse_error",
            "error": "Runner output is not valid JSON",
            "runtime_ms": 0,
            "stderr": stderr,
        }

    if "stderr" not in data:
        data["stderr"] = stderr
    return data
