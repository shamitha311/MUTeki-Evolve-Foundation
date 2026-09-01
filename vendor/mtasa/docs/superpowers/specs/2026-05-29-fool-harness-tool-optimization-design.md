# Fool Harness 工具优化（A 阶段）

日期：2026-05-29
状态：待评审

## 背景

`fool/harness/tools.py` 当前注册的工具集在两轮真实测试中暴露出以下缺陷：

1. `read_teacher_playbook` 与 system prefix 中已缓存的 playbook 重复，模型若调用则浪费一次 round-trip。
2. `read_last_report` / `read_incumbent_solver` 不带版本号语义；模型拿到的 best score 是一个数字，无法回看产生该分数的具体 solver/report，难以做横向对比。
3. `smoke_test_solver` 只做返回类型校验（list[tuple[str, str]]），不检查输出语义（task_id 是否在输入中、是否有重复分配）。
4. 没有"提交前预评分"工具。模型只能 `<final>` 提交后才能从 Genius 报告知道真实分数，回归在提交后才暴露。
5. 没有版本管理工具。`out/runs/<run_id>/solver_v003.py` 这类历史产物对模型不可见。

本次范围（A）：用最小改动补齐**版本化只读 + 本地预评分 + 输出语义校验**。不动 runner 主循环；不引入 sub-agent。

## 目标与非目标

**目标**
- 删除一个冗余工具，新增三个工具，强化一个工具。改完后模型可以：按版本号回看任意历史产物、在提交前用 Genius 本地评一个真实分数（仅 large_seed301）、smoke test 同时验证语义。
- 所有改动落在 `fool/harness/tools.py` 内；`fool/harness/prompt.py` 仅需要在工具列表自动生成处随之更新（tool spec 由 registry 反射，无需手改）。

**非目标**
- 不加 sub-agent 类工具（如 `analyze_score_delta`、`diff_solvers`）。留到 B 阶段。
- 不重构 `out/runs/<run_id>/` 目录结构（不建 `index.json`）。留到 C 阶段。
- 不改变 `<final>` 后的提交/记忆/分类逻辑。

## 变更清单

### 删除
- `read_teacher_playbook` 工具（`tools.py:143-145, 202-208`）。playbook 已在 `prompt.py:79-80` 进 system prefix 缓存，工具是冗余路径。

### 新增

#### 1. `read_version(v: int, kind: "solver" | "report" | "plan")`
按版本号读历史产物，文件解析约定如下：
- `kind="solver"` → 读 `run_dir / f"solver_v{v:03d}.py"`，全文返回。
- `kind="report"` → 读 `run_dir / f"report_v{v:03d}.txt"`，全文返回（已经是文本报告，不需要裁剪；如果超长由 `max_output` 兜底）。
- `kind="plan"` → 读 `run_dir / f"harness_v{v:03d}.json"`，提取 `final.plan` 字段（dict）并以 `json.dumps(..., ensure_ascii=False, indent=2)` 返回；若文件存在但无 `final.plan`，返回 `"(no plan recorded for v{v:03d})"`。

错误情况：
- `v` 非正整数 / `kind` 不在允许集合 → `ok=False`，附明确错误信息。
- 文件不存在 → `ok=False`，返回 `"v{v:03d} {kind} not found"`，不抛异常。
- 当前轮号 `state.iteration` 未通过 `ToolContext` 暴露，工具不强制校验 `v < current_iteration`；让 "not found" 作为天然边界。

`max_output`：对 `report` 设 8000 字符兜底；其余不裁剪。

#### 2. `score_locally()`
对当前 draft（`run_dir / "draft.py"`）在 `data/official/large_seed301.txt` 上跑一次 Genius 评分，返回该 case 的得分摘要。**不写入 `solver_vNNN.py`**，不影响最优状态、不进入记忆。

实现要点：
- 通过 `subprocess` 调用 `genius/run_submission.py`（与正式提交同一路径），传入 `--solver draft.py --input-dir <临时目录只含 large_seed301.txt>`，`--report <run_dir>/_local_preview.txt`。临时目录通过 `tempfile.TemporaryDirectory()` 创建，把 `data/official/large_seed301.txt` 软链/复制进去。
- 评分模式由 Genius 端固定（`FIXED_SCORING_MODE`），工具不传 scoring 参数。
- 解析返回的 `_local_preview.txt`：用现成的 `fool.genius_file_client.read_report` 解析；从 `summary` 抽 `total_score` 和 `uncovered_tasks`，组装成形如 `"local_preview large_seed301: total_score=12345.67 uncovered=3"` 的一行摘要 + 报告头 30 行原文。
- 超时：整体超过 60s 则 `ok=False`，返回 `"local preview timed out"`。
- `data/official/large_seed301.txt` 缺失 → `ok=False`，明确提示而不是悄悄退化。
- `max_output`：3000 字符。

`risky=False`（不修改 draft、不动 best/记忆，但有 IO 副作用——临时目录与 `_local_preview.txt`）。

#### 3. （强化）`smoke_test_solver`
当前 `_SMOKE_HARNESS` 只验证形状。新增两条**输出语义**校验：

- 解析 sample 输入，收集所有合法 `task_id`（注意：`task_id_list` 列以逗号拆开后每个 token 都是合法 id）与 `courier_id`。
- 校验：solve 返回的每个 `(task_id, courier_id)` 中，`task_id` 与 `courier_id` 都必须出现在输入集合中；`task_id` 在整个返回列表中不能重复出现（同一任务不能分配给两个人）。
- 任何一条违反，verdict 改为 `{"ok": False, "msg": "semantic check failed: <具体原因>"}`。
- 形状校验保持不变，且语义校验出现在形状校验之后（形状错了不再判语义）。

保留单 case、保留 10s 超时。harness 输出 JSON 协议不变。

### 不变
- `read_last_report` / `read_incumbent_solver` / `profile_dataset` / `rank_bottlenecks` / `retrieve_memory` / `list_strategy_templates` / `draft_solver` / `patch_solver` / `read_current_draft` 保持现状。
- `ToolContext` 字段、`ToolRegistry` 行为、`prompt.py` 中的 system prefix 文本都不改（playbook 仍由 prefix 提供）。

## 数据流

```
模型 → score_locally()
  → tools.py 拷贝 large_seed301.txt 到临时目录
  → subprocess: python genius/run_submission.py --solver <run_dir>/draft.py --input-dir <tmp> --report <run_dir>/_local_preview.txt
  → read_report(_local_preview.txt) → 摘要
  → 返回模型
```

```
模型 → read_version(v=2, kind="plan")
  → 读 <run_dir>/harness_v002.json
  → 取 final.plan，json.dumps
  → 返回模型
```

## 风险与缓解

- **`score_locally` 跑 ~10s，且每轮可能被多次调用** → 工具内不缓存；由 prompt 文档建议"通常每轮 1 次"。记一行日志到 `fool.log`，便于事后审计调用频率。
- **临时目录残留** → 用 `with tempfile.TemporaryDirectory()` 上下文，异常路径也会清理。
- **`_local_preview.txt` 与正式 `report_vNNN.txt` 同目录** → 前缀加下划线，且不会被 `read_version` 匹配（后者按 `report_v\d{3}\.txt` 严格命名）。需要在 `frontend/server.py:_purge_previous_outputs` 中确认会被一并清理（它已经在清 `out/runs/` 整目录，没问题）。
- **`read_version` 让模型回看老的失败版本** → 这是优点，不是风险。memory_store 已经会标注 regressed 版本，模型本就该能看到。

## 测试计划

`genius/tests/test_harness_tools.py` 已有的覆盖保持通过；新增用例：

1. `read_version` 三种 kind 的正常路径、`v` 不存在的 not-found 分支、非法 kind/非法 v 的错误分支。
2. `score_locally` 正常路径（用一个能跑通的 solver 模板 + 真实 `large_seed301.txt`）→ 摘要里能解析出 `total_score=` 数字；以及缺失数据集时的 fail 分支。
3. `smoke_test_solver` 语义校验：构造一个会输出"不在输入里的 task_id"的 solver，断言 verdict 为 fail 并附正确原因；以及"重复分配同一 task_id"的 solver，断言相同。
4. 注册表中 `read_teacher_playbook` 已不存在；`read_version` / `score_locally` 已存在。

回归：`python -m pytest genius/tests` 全绿。

## 实现顺序

1. 删 `read_teacher_playbook` + 跑测试看哪些用例需要清理。
2. 加 `read_version`，含三种 kind 与错误分支。
3. 加 `score_locally`，先打通子进程与报告解析，再加超时与摘要格式化。
4. 强化 `_SMOKE_HARNESS`（注意：它是嵌在 `tools.py` 里的字符串，靠子进程执行，所以语义校验代码要内联到那段 raw string 里——不能 import `tools.py` 自身）。
5. 补测试。

每一步独立可提交；不需要在中间状态做大幅度的 prompt 改写。
