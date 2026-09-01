# Fool Loop → Harness 重构设计

**日期**：2026-05-29
**作者**：sunnyseed + Claude
**参考**：`/Users/zhuym/Documents/101camp/meituan/mini-coding-agent/mini_coding_agent.py`

## 核心理念

外层只做**循环、评分、记录**三件事；harness 是真正的工作区，LLM 通过工具自主决定每一步读什么、改什么、何时提交。

绝不把 teacher 全文、playbook、memory 摘要、dataset profile 预先塞进 prefix——那等于在 harness 外再造一个 pipeline，违背 harness 的意义。所有上下文都通过工具按需取。

## 范围

替换 `fool/fool_loop.py` 中 `_reflect_and_plan` + `_propose_solver` + `_fallback_reflection_plan` + 相关辅助（约 600 行）为单个 harness 会话调用。一次性替换，无回退开关。

## 外层（保留，只这些）

```
run_fool_loop(...):
  for i in 1..iterations:
    if stop_event: break
    state = RoundState(
        iteration=i,
        best_score, best_solver_path, best_report_path,
        recent_history=last_3_outcomes,
        input_dir, run_dir,
    )
    try:
      result = harness.run_round(state, model_client)
    except HarnessFailure as e:
      record_outcome(i, score=None, hypothesis="", outcome="harness_failed")
      continue

    solver_path = run_dir / f"solver_v{i:03d}.py"
    solver_path.write_text(result.solver_code)
    report = submit_to_genius(solver_path, input_dir)
    score = report.total_score

    outcome = classify(score, best_score)   # improved / regressed / catastrophic
    if outcome == "improved":
      best_score, best_solver_path, best_report_path = score, solver_path, report_path

    record_outcome(i, score, result.plan["hypothesis"], outcome)
    durable_memory.record_round(result, report, outcome)
```

**外层做的全部纠偏**：
- best 指针**单向前进**（新分严格优于才更新）
- catastrophic 仅打标，不替换 incumbent、不重试本轮
- harness 抛 `HarnessFailure` → 记一行 `harness_failed`，下一轮继续

**外层不做**：
- 不读 teacher / playbook
- 不预算 change_ratio / strategy_lane / blocked_hypotheses / target_buckets
- 不调 judge_fitter
- 不做 precheck 重试
- 不做 same-iteration rollback
- 不分 bootstrap 首轮（首轮 `best_score=None`，harness 自然处理）

### `recent_history` 是外层唯一携带的"记忆"

每条仅 4 字段：`{i, score, hypothesis, outcome}`。最多 3 条。harness 在 prefix 中看到这 3 条即可判断"我刚才在干什么、有没有退步、要不要换方向"。durable_memory 的厚记忆通过 `retrieve_memory` 工具按需取。

## Harness 内部

### Prefix（稳定，吃 prompt cache）

只包含：
1. 身份与目标
2. 硬约束（CLAUDE.md 中的运行时约束逐条列出）
3. 工具清单（名称、schema、是否 risky、一句描述）
4. 输出格式：`<tool>...</tool>` 或 `<final>...</final>`

**不包含**：teacher 全文、playbook、dataset profile、memory 摘要、当前 strategy_lane、blocked_hypotheses、target_buckets——这些全由工具按需取。

### 每轮注入（轻量）

prefix 之后追加：
```
Round: <i>
Best score so far: <best_score or "none">
Recent rounds:
  i=<n-2> score=<...> hypothesis=<...> outcome=<...>
  i=<n-1> score=<...> hypothesis=<...> outcome=<...>
  i=<n-0> score=<...> hypothesis=<...> outcome=<...>

Transcript so far:
  <tool calls and results from THIS round>
```

### 工具清单

只读：

| 名称 | 参数 |
|---|---|
| `read_teacher_checklist` | — |
| `read_teacher_playbook` | — |
| `read_last_report` | — |
| `read_incumbent_solver` | — |
| `profile_dataset` | — |
| `rank_bottlenecks` | `top_k=4` |
| `retrieve_memory` | `query`, `target_buckets?` |
| `list_strategy_templates` | — |

编辑（写 `run_dir/draft.py`）：

| 名称 | 参数 |
|---|---|
| `draft_solver` | `code` |
| `patch_solver` | `old_text`, `new_text`（必须唯一匹配）|
| `smoke_test_solver` | — （用 py3.9 跑 1 个 sample case，限时 10s，校验 `solve` 返回 `list[tuple[str,str]]`）|

终止：

LLM 输出
```xml
<final>
  <plan>{"hypothesis": "...", "analysis": "...", "target_buckets": [...], "edit_plan": [...]}</plan>
</final>
```

harness 取当前草稿为 solver_code，`<plan>` JSON 为 plan，返回外层。`submit_solver` 不暴露给 LLM——评分是外层的事。

`read_incumbent_solver` 内部规则：best 存在则返回 best；否则返回 bootstrap path（如 CLI 传了）；否则返回 `fool/templates/solver_greedy.py`。bootstrap 处理就消化在这里。

### 安全 / 终止

- `max_steps` 默认 12；超出后抛 `HarnessFailure("max_steps")`
- 连续两次同名同参工具 → 工具返回 error（沿用 mini）
- `draft_solver` / `patch_solver` 写入路径限定 `run_dir`
- `smoke_test_solver` 用 `subprocess.run([python3.9, draft_path, ...], timeout=10)`
- LLM 输出既无 `<tool>` 也无 `<final>` → 注入 retry notice，最多 `max_steps * 2` 次后抛 `HarnessFailure("malformed")`

### Session 落盘

`out/runs/<run_id>/harness_v{i:03d}.json`：
```json
{
  "iteration": i,
  "round_state": {...},
  "transcript": [{role, name?, args?, content, ts}, ...],
  "final": {"solver_code": "...", "plan": {...}}
}
```

不实现 resume；只为复盘和调试。

## 文件改动

**新增**：
- `fool/harness/__init__.py` — `run_round`, `RoundState`, `HarnessResult`, `HarnessFailure`
- `fool/harness/context.py` — dataclasses
- `fool/harness/prompt.py` — `build_prefix(tools)`, `build_round_message(state, transcript)`
- `fool/harness/tools.py` — 11 个工具实现 + registry
- `fool/harness/parser.py` — `<tool>` / `<final>` 解析（移植 mini 的 parse + parse_xml_tool）
- `fool/harness/session.py` — JSON 落盘
- `fool/harness/runner.py` — 主循环
- `genius/tests/test_harness_runner.py` — `FakeModelClient` 驱动确定性测试

**修改**：
- `fool/fool_loop.py`：删除 `_reflect_and_plan`, `_propose_solver`, `_fallback_reflection_plan`, `_safe_parse_json_object`, `_is_valid_reflection_plan`, `_normalize_reflection_plan`, `_summarize_reflection_memory`, `_recent_failed_hypotheses`, `_pick_strategy_lane`, `_resolve_round2_lane`, `_build_portfolio_focus_policy`, `_recent_non_improving_streak`, `_run_large301_precheck`, `_record_tool_results`, `_render_template_reference`, `_solver_change_ratio`, `_catastrophic_regression_reason` 中外层判定以外的部分；主循环改为上面的精简版本
- 删除外层与 `judge_fitter`、`select_strategy_templates`、`blocked_hypotheses` 相关的全部预处理代码
- `fool/llm_client.py`：抽出 `ModelClient` 接口（`complete(prompt, max_new_tokens) -> str`），harness 走这个接口；旧 `call_llm` 仅 probe 时用
- `fool/agent_tools/`：保留 `analysis_tools.py`、`template_tools.py` 作为 harness 工具的底层实现

**删除的 CLI flags**（不再有意义）：
- `--round2-strategy-lane` / `--large301-precheck-retries` / `--same-iteration-rollback-retries` / `--solver-round-max-tokens` 的分轮表语义（保留单值 max_new_tokens 即可）

## 测试

1. **单元**：`FakeModelClient` 喂固定 `<tool>`/`<final>` 序列，验证 parser、tool registry、session 落盘、max_steps 终止、重复调用拦截
2. **smoke_test_solver**：用一个故意 broken 的 draft 验证检测能力
3. **集成**：`sample_10_cases` 上跑 5 轮真 LLM，断言 (a) 至少一轮 improved，(b) 外层从不替换 incumbent 在非 improved 时，(c) harness JSON 完整落盘

## 风险

- **LLM 不收敛**：max_steps=12 + 重复拦截 + retry 上限。超限抛失败，进下一轮。
- **token 成本**：prefix 极薄，吃满 prompt cache；transcript clip。
- **一次性替换不可回滚**：`git revert` 是兜底。

## 非目标

- 不实现 session resume / delegation
- 不动 frontend、teacher review、scoreboard、Genius
- 不改 scoring 模式与运行时约束
