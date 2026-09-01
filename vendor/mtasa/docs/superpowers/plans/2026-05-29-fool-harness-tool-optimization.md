# Fool Harness 工具优化（A 阶段）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除冗余 `read_teacher_playbook`；新增 `read_version` 与 `score_locally` 两个工具；强化 `smoke_test_solver` 加输出语义校验。

**Architecture:** 全部改动局限在 `fool/harness/tools.py` 与 `genius/tests/test_harness_tools.py`。新工具与现有工具同构（`ToolSpec` 注册到 `build_default_registry`），不修改 `ToolContext`、`ToolRegistry`、runner、prompt prefix。`score_locally` 通过 `subprocess` 调 `genius/run_submission.py`（与正式提交同一路径），临时目录隔离单 case；语义校验内联到现有的 `_SMOKE_HARNESS` 字符串里（它在子进程中执行，不能 import 父进程模块）。

**Tech Stack:** Python 3.9+（harness 主进程）/ Python 3.9（solver 子进程）/ stdlib only / pytest。

---

## File Structure

- Modify: `fool/harness/tools.py`
  - 删除 `_t_read_teacher_playbook`、对应的 `ToolSpec`，以及顶部 `_TEACHER_PLAYBOOK` 常量。
  - 新增 `_t_read_version`、`_t_score_locally` 函数与两个 `ToolSpec`。
  - 修改 `_SMOKE_HARNESS` 字符串：加输入解析 + 语义校验段。
- Modify: `genius/tests/test_harness_tools.py`
  - 删掉/调整任何引用 `read_teacher_playbook` 的断言（当前文件没有，但 spec 项要求确认）。
  - 新增 `read_version`（3 个 kind × 正常+缺失 + 非法参数）测试。
  - 新增 `score_locally` 正常路径与缺失数据集测试。
  - 新增 `smoke_test_solver` 语义校验两种失败场景测试。

无新文件创建。

---

## Task 1: 删除 `read_teacher_playbook` 工具

**Files:**
- Modify: `fool/harness/tools.py:112` (删 `_TEACHER_PLAYBOOK` 常量)
- Modify: `fool/harness/tools.py:143-145` (删 `_t_read_teacher_playbook` 函数)
- Modify: `fool/harness/tools.py:202-208` (删 `_READ_ONLY_SPECS` 中 `read_teacher_playbook` 这一项)
- Test: `genius/tests/test_harness_tools.py` (新增注册表反向断言)

- [ ] **Step 1: 写失败测试**

加入 `genius/tests/test_harness_tools.py` 末尾：

```python
def test_read_teacher_playbook_is_not_registered() -> None:
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.specs()}
    assert "read_teacher_playbook" not in names
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest genius/tests/test_harness_tools.py::test_read_teacher_playbook_is_not_registered -v`
Expected: FAIL（当前仍注册了该工具）。

- [ ] **Step 3: 删除 `_TEACHER_PLAYBOOK` 常量**

打开 `fool/harness/tools.py`，删除以下行（约 112 行）：

```python
_TEACHER_PLAYBOOK = _FOOL_ROOT / "teacher" / "DATA_STRATEGY_PLAYBOOK.md"
```

保留下一行的 `_GREEDY_TEMPLATE`。

- [ ] **Step 4: 删除 `_t_read_teacher_playbook` 函数**

删除：

```python
def _t_read_teacher_playbook(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return _read_text_or_notice(_TEACHER_PLAYBOOK, "teacher playbook")
```

- [ ] **Step 5: 从 `_READ_ONLY_SPECS` 删 `read_teacher_playbook` 条目**

删除该 `ToolSpec(...)` 整段（从 `name="read_teacher_playbook"` 到对应的 `),`）。

- [ ] **Step 6: 跑全测试**

Run: `python -m pytest genius/tests -v`
Expected: 全部 PASS，包含 Step 1 新增的用例。

- [ ] **Step 7: 提交**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/tools: drop redundant read_teacher_playbook (playbook is in system prefix)"
```

---

## Task 2: 新增 `read_version` 工具

**Files:**
- Modify: `fool/harness/tools.py`（在 `_t_list_strategy_templates` 之后新增 `_t_read_version`；在 `_READ_ONLY_SPECS` 末尾追加 ToolSpec）
- Test: `genius/tests/test_harness_tools.py`

- [ ] **Step 1: 写失败测试 — 三种 kind 正常路径**

加入测试文件末尾：

```python
def test_read_version_returns_solver_by_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "solver_v002.py").write_text("# solver v2\ndef solve(t): return []\n")
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 2, "kind": "solver"})
    assert result.ok is True
    assert "# solver v2" in result.content


def test_read_version_returns_report_by_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "report_v003.txt").write_text("Average Penalty Score\n12.34\n")
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 3, "kind": "report"})
    assert result.ok is True
    assert "12.34" in result.content


def test_read_version_returns_plan_by_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    payload = {
        "final": {
            "plan": {
                "hypothesis": "try greedy by willingness",
                "analysis": "low_w bucket is the bottleneck",
                "target_buckets": ["low_w_seed501"],
                "edit_plan": ["sort by willingness desc"],
            }
        }
    }
    (run_dir / "harness_v001.json").write_text(_json.dumps(payload))
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 1, "kind": "plan"})
    assert result.ok is True
    assert "try greedy by willingness" in result.content
    assert "low_w_seed501" in result.content
```

- [ ] **Step 2: 写失败测试 — 边界情况**

继续追加：

```python
def test_read_version_not_found_returns_fail(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 9, "kind": "solver"})
    assert result.ok is False
    assert "v009" in result.content
    assert "not found" in result.content


def test_read_version_rejects_invalid_kind(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 1, "kind": "garbage"})
    assert result.ok is False
    assert "kind" in result.content


def test_read_version_rejects_non_positive_v(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 0, "kind": "solver"})
    assert result.ok is False
    assert "v" in result.content


def test_read_version_plan_missing_returns_notice(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "harness_v002.json").write_text(_json.dumps({"final": {}}))
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 2, "kind": "plan"})
    assert result.ok is True
    assert "no plan" in result.content.lower()
```

- [ ] **Step 3: 跑测试验证失败**

Run: `python -m pytest genius/tests/test_harness_tools.py -k read_version -v`
Expected: 全部 FAIL（`unknown tool 'read_version'`）。

- [ ] **Step 4: 实现 `_t_read_version` 并注册**

在 `fool/harness/tools.py` 的 `_t_list_strategy_templates` 函数定义之后，插入：

```python
_VALID_VERSION_KINDS = ("solver", "report", "plan")


def _t_read_version(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    v = args.get("v")
    kind = args.get("kind")
    if not isinstance(v, int) or v <= 0:
        return ToolResult(ok=False, content="error: 'v' must be a positive integer")
    if kind not in _VALID_VERSION_KINDS:
        return ToolResult(
            ok=False,
            content=f"error: 'kind' must be one of {list(_VALID_VERSION_KINDS)}",
        )
    tag = f"v{v:03d}"
    if kind == "solver":
        path = ctx.run_dir / f"solver_{tag}.py"
    elif kind == "report":
        path = ctx.run_dir / f"report_{tag}.txt"
    else:
        path = ctx.run_dir / f"harness_{tag}.json"

    if not path.exists():
        return ToolResult(ok=False, content=f"{tag} {kind} not found")

    if kind == "plan":
        import json as _json_local

        try:
            data = _json_local.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ToolResult(ok=False, content=f"error: cannot parse {path.name}: {exc}")
        plan = ((data or {}).get("final") or {}).get("plan")
        if not plan:
            return ToolResult(ok=True, content=f"(no plan recorded for {tag})")
        return ToolResult(
            ok=True,
            content=_json_local.dumps(plan, ensure_ascii=False, indent=2),
        )

    return ToolResult(ok=True, content=path.read_text(encoding="utf-8", errors="replace"))
```

在 `_READ_ONLY_SPECS` 列表（紧接 `list_strategy_templates` 之后）追加：

```python
    ToolSpec(
        name="read_version",
        description="按版本号读取本次 run 内的历史 solver/report/plan",
        risky=False,
        schema={"v": "int", "kind": "str (solver|report|plan)"},
        run=_t_read_version,
        max_output=8000,
    ),
```

- [ ] **Step 5: 跑测试验证通过**

Run: `python -m pytest genius/tests/test_harness_tools.py -k read_version -v`
Expected: 7 个用例全部 PASS。

- [ ] **Step 6: 跑全测试**

Run: `python -m pytest genius/tests -v`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/tools: add read_version for per-version solver/report/plan lookup"
```

---

## Task 3: 强化 `smoke_test_solver` 加输出语义校验

**Files:**
- Modify: `fool/harness/tools.py:312-353` (`_SMOKE_HARNESS` 字符串)
- Test: `genius/tests/test_harness_tools.py`

注意：`_SMOKE_HARNESS` 是嵌入式 Python 源代码字符串，由子进程执行。**不能 `import` 父进程的 `tools.py`**；所有解析逻辑必须内联在字符串里。

- [ ] **Step 1: 写失败测试 — 拒绝不存在的 task_id**

加入测试文件末尾：

```python
def test_smoke_test_solver_fails_on_unknown_task_id(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return [('t_unknown','c1')]\n"},
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "unknown task_id" in result.content or "not in input" in result.content
```

- [ ] **Step 2: 写失败测试 — 拒绝重复的 task_id**

```python
def test_smoke_test_solver_fails_on_duplicate_task_id(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
        "t2\tc2\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return [('t1','c1'),('t1','c2')]\n"},
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "duplicate" in result.content.lower()
```

- [ ] **Step 3: 写测试 — 合并任务包里的子 task_id 应被认可**

```python
def test_smoke_test_solver_accepts_task_from_merged_bundle(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1,t2\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return [('t2','c1')]\n"},
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is True, result.content
```

- [ ] **Step 4: 跑测试验证失败**

Run: `python -m pytest genius/tests/test_harness_tools.py -k smoke_test -v`
Expected: 新加的 3 个用例 FAIL（当前 smoke 不做语义校验，第 3 个会通过但前两个会通过得过早）。

- [ ] **Step 5: 修改 `_SMOKE_HARNESS` 字符串**

替换 `fool/harness/tools.py:312` 起的 `_SMOKE_HARNESS` 字符串为：

```python
_SMOKE_HARNESS = r"""
import json
import sys
import importlib.util

draft_path = sys.argv[1]
sample_path = sys.argv[2]

spec = importlib.util.spec_from_file_location("draft", draft_path)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except Exception as exc:
    print(json.dumps({"ok": False, "msg": f"import error: {exc}"}))
    sys.exit(0)

if not hasattr(module, "solve") or not callable(module.solve):
    print(json.dumps({"ok": False, "msg": "missing callable solve()"}))
    sys.exit(0)

with open(sample_path, "r", encoding="utf-8") as fh:
    text = fh.read()

# Parse input to build the set of legal task_ids and courier_ids.
legal_tasks = set()
legal_couriers = set()
lines = text.splitlines()
for raw in lines[1:]:  # skip header
    if not raw.strip():
        continue
    cols = raw.split("\t")
    if len(cols) < 2:
        continue
    for tid in cols[0].split(","):
        tid = tid.strip()
        if tid:
            legal_tasks.add(tid)
    cid = cols[1].strip()
    if cid:
        legal_couriers.add(cid)

try:
    result = module.solve(text)
except Exception as exc:
    print(json.dumps({"ok": False, "msg": f"solve() raised: {exc}"}))
    sys.exit(0)

if not isinstance(result, list):
    print(json.dumps({"ok": False, "msg": "solve() did not return list"}))
    sys.exit(0)
for pair in result:
    if not (isinstance(pair, tuple) and len(pair) == 2):
        print(json.dumps({"ok": False, "msg": "solve() items must be 2-tuples"}))
        sys.exit(0)
    if not (isinstance(pair[0], str) and isinstance(pair[1], str)):
        print(json.dumps({"ok": False, "msg": "solve() tuples must be (str,str)"}))
        sys.exit(0)

# Semantic checks: ids must exist in input, no task_id may appear twice.
seen_tasks = set()
for tid, cid in result:
    if tid not in legal_tasks:
        print(json.dumps({"ok": False, "msg": f"semantic check failed: unknown task_id {tid!r} (not in input)"}))
        sys.exit(0)
    if cid not in legal_couriers:
        print(json.dumps({"ok": False, "msg": f"semantic check failed: unknown courier_id {cid!r} (not in input)"}))
        sys.exit(0)
    if tid in seen_tasks:
        print(json.dumps({"ok": False, "msg": f"semantic check failed: duplicate task_id {tid!r}"}))
        sys.exit(0)
    seen_tasks.add(tid)

print(json.dumps({"ok": True, "msg": f"PASS n={len(result)}"}))
"""
```

- [ ] **Step 6: 跑测试验证通过**

Run: `python -m pytest genius/tests/test_harness_tools.py -k smoke_test -v`
Expected: 全部 PASS（包括原有的 2 个 + 新增的 3 个）。

- [ ] **Step 7: 跑全测试**

Run: `python -m pytest genius/tests -v`
Expected: 全部 PASS。

- [ ] **Step 8: 提交**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/tools: smoke_test_solver also validates task/courier ids and duplicates"
```

---

## Task 4: 新增 `score_locally` 工具

**Files:**
- Modify: `fool/harness/tools.py`（在 `_t_smoke_test_solver` 之后新增 `_t_score_locally`；在 `_EDITOR_SPECS` 末尾追加 ToolSpec）
- Test: `genius/tests/test_harness_tools.py`

注意：`score_locally` 通过 subprocess 调 `genius/run_submission.py`，这条路径**真的会跑 python3.9 + 真实 large_seed301**。测试用真实数据集；如果该机器没有 `python3.9`，应在测试里用 `pytest.skip`。

- [ ] **Step 1: 写失败测试 — 数据集缺失**

加入测试文件末尾：

```python
def test_score_locally_fails_when_dataset_missing(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run("draft_solver", ctx, {"code": "def solve(t):\n    return []\n"})
    # Point the tool at a non-existent dataset.
    import fool.harness.tools as _tools
    monkeypatch.setattr(_tools, "_LARGE_SEED301", tmp_path / "missing.txt")
    result = registry.run("score_locally", ctx, {})
    assert result.ok is False
    assert "large_seed301" in result.content
```

- [ ] **Step 2: 写失败测试 — 没 draft**

```python
def test_score_locally_fails_without_draft(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("score_locally", ctx, {})
    assert result.ok is False
    assert "draft" in result.content.lower()
```

- [ ] **Step 3: 写测试 — 正常路径**

```python
import shutil as _shutil


def test_score_locally_runs_genius_and_returns_score(tmp_path: Path) -> None:
    if _shutil.which("python3.9") is None:
        import pytest
        pytest.skip("python3.9 not available on this host")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    # A trivially valid solver: empty assignment is shape-legal and Genius
    # will score it (penalty for full uncovered, but it returns a score).
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return []\n"},
    )
    result = registry.run("score_locally", ctx, {})
    assert result.ok is True, result.content
    assert "large_seed301" in result.content
    assert "total_score" in result.content or "Average Penalty Score" in result.content
    # Tool wrote the preview report alongside the draft.
    assert (run_dir / "_local_preview.txt").exists()
```

- [ ] **Step 4: 跑测试验证失败**

Run: `python -m pytest genius/tests/test_harness_tools.py -k score_locally -v`
Expected: 3 个用例 FAIL（`unknown tool 'score_locally'`）。

- [ ] **Step 5: 实现 `_t_score_locally`**

在 `fool/harness/tools.py` 的 `_t_smoke_test_solver` 函数之后插入：

```python
import tempfile as _tempfile


_LARGE_SEED301 = _FOOL_ROOT / "data" / "official" / "large_seed301.txt"
_RUN_SUBMISSION = _FOOL_ROOT / "genius" / "run_submission.py"
_LOCAL_PREVIEW_NAME = "_local_preview.txt"


def _t_score_locally(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    draft = _draft_path(ctx)
    if not draft.exists():
        return ToolResult(ok=False, content="error: no draft to score; call draft_solver first")
    if not _LARGE_SEED301.exists():
        return ToolResult(
            ok=False,
            content=f"error: large_seed301 dataset missing at {_LARGE_SEED301}",
        )

    preview_report = ctx.run_dir / _LOCAL_PREVIEW_NAME
    with _tempfile.TemporaryDirectory() as tmp:
        from shutil import copyfile

        case_dir = Path(tmp)
        copyfile(_LARGE_SEED301, case_dir / "large_seed301.txt")
        try:
            proc = _subprocess.run(
                [
                    _PY_CMD,
                    str(_RUN_SUBMISSION),
                    "--solver",
                    str(draft),
                    "--input-dir",
                    str(case_dir),
                    "--report",
                    str(preview_report),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except _subprocess.TimeoutExpired:
            return ToolResult(ok=False, content="local preview timed out after 60s")

    if proc.returncode != 0 or not preview_report.exists():
        return ToolResult(
            ok=False,
            content=f"error: run_submission exit={proc.returncode} stderr={proc.stderr.strip()[:400]}",
        )

    try:
        from fool.genius_file_client import read_report

        report = read_report(preview_report)
    except Exception as exc:
        return ToolResult(ok=False, content=f"error: cannot parse preview report: {exc}")

    case = (report.get("cases") or [{}])[0]
    summary_line = (
        f"local_preview large_seed301: "
        f"total_score={case.get('score')} uncovered={case.get('uncovered_tasks')} "
        f"covered={case.get('covered')}/{case.get('total_tasks')}"
    )
    head = preview_report.read_text(encoding="utf-8", errors="replace").splitlines()[:30]
    return ToolResult(ok=True, content=summary_line + "\n\n" + "\n".join(head))
```

在 `_EDITOR_SPECS` 列表末尾（`read_current_draft` 之后）追加：

```python
    ToolSpec(
        name="score_locally",
        description="在 data/official/large_seed301.txt 上用 Genius 给当前 draft 评一个预览分（不写入 solver_v*）",
        risky=False,
        schema={},
        run=_t_score_locally,
        max_output=3000,
    ),
```

- [ ] **Step 6: 跑测试验证通过**

Run: `python -m pytest genius/tests/test_harness_tools.py -k score_locally -v`
Expected: 3 个用例 PASS（在有 python3.9 的机器上）；缺 python3.9 时第 3 个 SKIP，前两个仍 PASS。

- [ ] **Step 7: 跑全测试**

Run: `python -m pytest genius/tests -v`
Expected: 全部 PASS。

- [ ] **Step 8: 提交**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/tools: add score_locally for pre-submit preview on large_seed301"
```

---

## Task 5: 端到端冒烟（手动）

**Files:** 无代码修改。

- [ ] **Step 1: 跑一个真实 fool loop，验证新工具能被 LLM 调到**

Run（假设已配置 OPENAI_API_KEY）:
```bash
python fool/fool_loop.py \
  --api-type openai --api-key "$OPENAI_API_KEY" --model gpt-4.1-mini \
  --iterations 2 --input-dir data/sample_10_cases --scoring official_like_latest
```

Expected:
- `out/runs/<run_id>/harness_v001.json` 里的 `messages` 中包含至少一次对 `read_version` / `score_locally` 之一的调用（**不强制**，但若 2 轮都没调用，则 prompt 可能需要补一句鼓励）。
- `out/runs/<run_id>/_local_preview.txt` 若被生成，能解析。
- 不应出现对 `read_teacher_playbook` 的调用（已删）。

- [ ] **Step 2: 若 LLM 始终不调用新工具，记一条 followup**

若观察到 LLM 没有调用 `score_locally`，在本仓库根目录新建一个 issue 或者在 `docs/superpowers/specs/` 的下一个 spec 里记一条："考虑在 prompt `_OUTPUT_RULES` 中增加：在 `<final>` 之前应该用 `score_locally` 做一次本地预评分"。本任务**不修改** prompt，留到下次迭代评估。

---

## Self-Review

**1. Spec coverage:**
- 删 `read_teacher_playbook` → Task 1 ✓
- `read_version(v, kind∈{solver,report,plan})` → Task 2，含三种 kind 与四种错误分支 ✓
- `score_locally` 默认 large_seed301、子进程跑 `run_submission.py`、`_local_preview.txt`、缺失数据集 fail、60s 超时 → Task 4 ✓
- smoke 加 task_id/courier_id 在输入集合中 + 无重复 task_id → Task 3 ✓
- 不动 `ToolContext`/runner/prompt prefix → 所有 task 范围都在 tools.py + 测试 ✓
- 测试：`read_version` 三种 kind 正常+缺失+非法参数；`score_locally` 缺数据集 + 正常路径；`smoke` 两类语义失败 → 全覆盖 ✓

**2. Placeholder scan:** 无 TBD/TODO/省略；每个有代码的 step 都给了完整代码或完整命令 ✓

**3. Type consistency:** `_LARGE_SEED301`、`_RUN_SUBMISSION`、`_LOCAL_PREVIEW_NAME`、`_VALID_VERSION_KINDS` 在 Task 2/4 中一处定义、一处使用；`_t_read_version` / `_t_score_locally` 函数名前缀与现有惯例 `_t_*` 一致；`read_version` / `score_locally` 工具名在 plan、测试断言与 `ToolSpec.name` 三处一致 ✓
