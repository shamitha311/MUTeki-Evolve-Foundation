from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fool.harness import _block_patch_prompts
from fool.harness.context import RoundState
from fool.harness.hypothesis_classes import recent_hypothesis_classes
from fool.harness.tools import ToolRegistry

# Registry of per-tool prompt fragments. Convention: tools whose usage needs
# more than a one-line ToolSpec.description live in `_<tool>_prompts.py` and
# expose a `SECTION` string. build_prefix appends each registered fragment
# after the generic OUTPUT_RULES, keeping tool-specific prose out of the
# shared body. To add a new one: create `_<tool>_prompts.py`, import it here,
# and add an entry below.
_TOOL_PROMPT_SECTIONS: list[tuple[str, str]] = [
    ("block_patch", _block_patch_prompts.SECTION),
]

_PLAYBOOK_PATH = Path(__file__).resolve().parents[2] / "teacher" / "DATA_STRATEGY_PLAYBOOK.md"


def _load_playbook() -> str:
    try:
        return _PLAYBOOK_PATH.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


_PLAYBOOK_CACHED = _load_playbook()

_HARD_CONSTRAINTS = """\
硬性运行约束（由 "Genius" 打分器 强制执行，绝不能绕过）：
- Solver 在 python3.6 环境下运行，仅允许使用 Python 标准库（不能使用 numpy、OR-Tools、CP-SAT）。
  注意：Python 3.6 不支持 PEP 585 泛型下标（不要写 `list[...]`、`tuple[...]`、`set[...]`、`dict[...]`），
  也不支持 `int.bit_count()`（请用 `bin(x).count('1')`）。
- **绝对禁止 `from typing import ...` 与 `import typing`**（任何位置）。线上沙箱观测到这条 import 会
  让整张表 10/10 case 全 error。需要类型注解时只写裸内置名（`list`/`tuple`/`set`/`dict`）或干脆不写。
  smoke 静态门会拦截。
- Solver 顶层 import 只允许：`import time`、`import random`、`import heapq`、`from collections import defaultdict`。
  禁止 `from __future__ import ...`、`from typing import ...`，也不要使用 math/bisect/itertools 等其他顶层 import。
- Solver 入口函数签名固定为：`def solve(input_text: str) -> list:`
    返回类型注解必须是裸 `list`，不允许 `List[Tuple[str, str]]` 等任何下标形式。
    返回值结构：`[(task_id_list_str, [courier_id, ...]), ...]` —— 每行 (str, list-of-str)。
- 输入为 TAB 分隔，共 4 列：task_id_list、courier_id、total_score、willingness。
    task_id_list 中可能包含逗号（表示合并任务包）；这些逗号 不是 CSV 分隔符。
- 同一个 task_id 只能使用一次。
- 同一个 courier_id 只能使用一次。
- 评分方式为 official_like_latest。不要自行发明评分开关。
- **时间预算硬契约（协议常量 `BUDGET_SEC`）**：
  solver 必须在模块顶层声明 `BUDGET_SEC = 10.0`（一次，**完全是 `10.0` 这个字面量**，不要写
  `9.5`、`9`、`10`、`9.0`、注释里改成别的等），并在 `solve` 内基于它计算 deadline 自限：
  ```
  BUDGET_SEC = 10.0  # 协议常量；Genius 本地会改写它，不要自行修改
  def solve(input_text: str) -> list:
      import time
      deadline = time.monotonic() + BUDGET_SEC - 0.5  # 0.5s 安全裕量，可调
      # ...每个循环/分支前判断 time.monotonic() >= deadline 则提前返回当前最优解
  ```
  - 10.0 = 美团线上每 case wall 上限。本地 Genius 跑得慢 ~2.5×，会自动把 `BUDGET_SEC`
    改写为本地时限（默认 25.0）；solver 里写其它任何值都会被 smoke 拒绝并整轮报废。
  - 想留更多 buffer 就改 `- 0.5` 那一项（本地按比例自动放大），不要改 BUDGET_SEC 本身。
  - smoke 静态门会验证：(a) 顶层恰好一次 `BUDGET_SEC = 10.0`；(b) solve 中至少一次
    `time.monotonic()` 或 `time.time()` 调用。仅出现一次 import time 而无 deadline 检查不算合格。
- **输出层硬契约（绝不可省略，绝不可"信任算法不会 dup"而绕过）**：
  solve 必须以 `return _finalize(result)` 结束。`_finalize` 是模板中已固定的
  ~15 行去重函数：按出现顺序贪心保留先出现的行，丢弃任何跨行重复 courier
  或 task 的整行，并丢弃空 bundle 行。函数名必须为 `_finalize`（便于审计）。
  - 即使你确信本轮算法的不变量保证无 dup，这一层仍必须保留。理由：
    swap / LNS / chain-reopt 中的 stale-snapshot bug 极难自查；一旦发生
    跨行重复，Genius 把该 case 直接判最大惩罚 100*total_tasks，**整轮报废**。
  - 如果上一轮 incumbent 的 solve 末尾没有 `_finalize`，本轮必须补上。
  - 不要"内联"或"展开" `_finalize` —— 它是仓库级契约，保留函数边界便于
    后续轮次稳定继承。
"""

_SMOKE_VS_SUBMIT = """\
本地 smoke 预览 vs Genius 提交评分（重要——绝不能等同看待）：
- `smoke_test_solver` 的 local_preview 跑的是 10-case 离线预览集，**与 Genius 提交时实际评测的
  case 集合 分差可达 ±20%。
- 最终成绩的标准是：Genius 桶级证据明确改善 → 信桶级证据。
"""

_MEMORY_PROTOCOL = """\
## Memory Protocol

**outcome 以桶为单位判定**（重要）：
- 看 `target_buckets` 中每个桶是否超过该桶自身 incumbent（带宽 0.3%）。

**写记忆**：
本轮内在发现稳定约束，或重大架构决策时手动调 `memory_write`。
"""

_TOOL_USAGE_NOTES = """\
## 工具使用说明

**截断续读**：工具结果末尾出现 `<<<TRUNCATED>>>` 时，用 `read_tool_result(uuid=..., start_line=N)` 续读，不要在截断处下结论。

**版本检索**（每轮提交分配一个跨 run 全局唯一 v）：
- `list_versions(scope=current_run|all|best)` — 列版本表；`kinds` 列 `SRPr` 表示 solver/report/plan/reflect 是否可读。
- `read_version(v=<n>, kind=solver|report|plan|reflect|harness_full)` — v 接受 int / `'best'` / `'latest'` / 负数（本 run 倒数）。

**桶分查询**：
- `bucket_scoreboard()` — 各桶最优 v 总览；`bucket_scoreboard(v=N)` — 该 v 所有桶 Δ vs incumbent。
"""

_HYPOTHESIS_TAXONOMY = """\
## Hypothesis class taxonomy

"""


_OUTPUT_RULES = """\
Output rules（每一步都要严格遵守输出格式与顺序）:

- 每一步的回复按以下顺序输出：
  1) 先输出 <intent>...</intent>： ≤300 字符 的简短中文
     - 写法：动词开头、单句陈述"做什么 / 为什么 / 期望信号"。
     - 这是你自己的工作记录，写成可被复盘的内容，不要空话，不要写给人看的解释段。深度推理不在这里展开，留到 <final>.analysis 的篇幅里写结论。
    
     仅在每一轮的**首条回复**里，<intent> **必须**额外包含"递进式复盘"四段：
       (a) 上一轮我学到了什么（基于 Prior round 的 plan/report）；
       (b) 我决定保留什么；
       (c) 我决定放弃什么；
       (d) 本轮要测的单一机制是什么。
     后续步骤的 <intent> 简写这一步在做什么、为什么，不要重复 recap。
  2) 紧跟 <intent> 后面（或直接），**必须**输出 EXACTLY ONE 工具调用或终止标签
     — 这是硬性要求：你是 Agent，不是问答助手，每一步都要"动手"。不要仅输出 `<intent>`。
     - 反向同样硬性：若一步**没有** `<intent>` 就直接发 `<tool>` 或 `<final>`，runtime 会拒绝执行（计入 malformed、占用步预算）并要求你补一句 `<intent>` 后重发。intent 是这一步动作的事前说明，不能省略。
     - 工具调用：<tool name="TOOL_NAME"><args>{...}</args></tool>
       block_patch 专用：<tool name="block_patch"><blocks>...</blocks></tool>
     - 终止形式：<final><plan>{"hypothesis":"...","analysis":"...","target_buckets":[...],"edit_plan":[...]}</plan></final>
- 当你输出 <final> 时，harness 会将当前的 draft.py 作为 solver，并提交到 Genius。
- 不得编造工具结果。不得使用相同参数重复调用同一个工具。
- `block_patch` 的返回值已包含每个改动区周边的 post-patch 预览（±2 行）；**不要紧接着调用 `read_current_draft` 复查刚改的位置**，预览已能验证落点。
- block_patch 是 draft 编辑的**唯一**工具；详细规则与示例见下方"block_patch 行为说明"与"block_patch 示例"。
"""

_IDENTITY = """\
你是一个 **AutoSolver Agent** 系统，通过自主策略探索、调用工具、迭代改进循环，求解下面这个问题的最优解：

> 给定一组配送订单（tasks）和可用骑手，以及每个任务-骑手组合的预计算分数，
> 求一个最优分配方案，使**接单订单数量最大化**，同时**总分数（罚分）最小化**。

你**不是**一个问答助手，也不是一个解释器：
- 你的每一步输出**必须**携带一个工具调用 `<tool>...</tool>` 或终止标签 `<final>...</final>`；
  仅输出 `<intent>`（或纯自然语言、纯分析）**不算有效步骤**，会被 harness 视为空转并立即追问。
- 你通过反复调用工具（读上下文 / 改 draft / smoke）逼近最优解，
  不是通过给"用户"讲解思路得到答案。

**评分方向（不要搞反）**：
- `total_score` 、 `average_score` 是**罚分（penalty）**，**越低越好**。

每一轮由你主动推进：
- 提出改进计划；
- 用 tools 取上下文相关信息；
    - 本轮 draft.py 的当前内容已嵌入 round header，无需再额外读取。
    - 用 block_patch 修改 draft.py；
- 用 smoke_test_solver 验证；
- 重要：瓶颈/停滞时禁止把回滚提交当作终局；必须推进可验证改动。
- 当你准备好时，输出 <final>，随后 harness 会提交该版本，获得各 cases 的完整评分。
"""


def build_prefix(registry: ToolRegistry) -> str:
    tool_lines: list[str] = []
    for spec in registry.specs():
        schema_parts = ", ".join(f"{k}: {v}" for k, v in spec["schema"].items())
        risk = "risky" if spec["risky"] else "safe"
        tool_lines.append(f"- {spec['name']}({schema_parts}) [{risk}] {spec['description']}")
    tools_block = "Tools:\n" + "\n".join(tool_lines)

    parts = [
        _IDENTITY.strip(),
        _HARD_CONSTRAINTS.strip(),
        _SMOKE_VS_SUBMIT.strip(),
        tools_block,
        _OUTPUT_RULES.strip(),
        _MEMORY_PROTOCOL.strip(),
        _TOOL_USAGE_NOTES.strip(),
        _HYPOTHESIS_TAXONOMY.strip(),
    ]
    registered = {spec["name"] for spec in registry.specs()}
    for tool_name, section in _TOOL_PROMPT_SECTIONS:
        if tool_name in registered and section.strip():
            parts.append(section.strip())
    if _PLAYBOOK_CACHED:
        parts.append("Data strategy playbook (static reference):\n" + _PLAYBOOK_CACHED)
    return "\n\n".join(parts)


def build_round_header(
    state: RoundState,
    *,
    memory_index_path: Path | None = None,
    teacher_review_block: str | None = None,
) -> str:
    """Per-round initial user message: round index + best score + recent history.

    For rounds N>1, also inject a summary of the previous round's final plan
    and report so the model can write an evidence-based recap in its first
    <intent>. Stable for the duration of the round, so it can sit as a single
    user turn while subsequent tool results append as their own user turns.
    """
    best = (
        "none"
        if state.best_score is None
        else f"{state.best_score} (penalty — lower is better; aim to reduce this)"
    )
    if state.recent_history:
        history_lines = [
            f"  i={item.iteration} score={item.score} "
            f"hypothesis={item.hypothesis!r} outcome={item.outcome}"
            for item in state.recent_history
        ]
        history_block = "Recent rounds:\n" + "\n".join(history_lines)
        if any(item.outcome == "duplicate_skipped" for item in state.recent_history):
            history_block += (
                "\nNote: outcome=duplicate_skipped means the submitted solver was a"
                " near-duplicate of incumbent — Genius was NOT actually run; the score"
                " shown is the cached incumbent, not an evaluation result. Treat as"
                " 'no signal'. Next round MUST make a substantive change (not a tiny"
                " textual tweak) or pivot strategy entirely."
            )
        classes = recent_hypothesis_classes(state, window=5)
        if classes:
            class_line = ", ".join(f"i={i}:{c}" for i, c in classes)
            history_block += (
                "\nRecent hypothesis classes (passive hint, no enforcement; "
                "see Hypothesis class taxonomy in prefix): "
                + class_line
            )
    else:
        history_block = "Recent rounds: (none yet)"

    parts = [
        f"Round: {state.iteration}",
        f"Best score so far: {best}",
        history_block,
    ]

    if teacher_review_block:
        # Out-of-loop periodic review verdict (suggestion only — model is
        # free to ignore). Sits above Recent rounds so it gets first glance.
        parts.insert(0, teacher_review_block)

    if memory_index_path is not None and Path(memory_index_path).is_file():
        try:
            idx_lines = Path(memory_index_path).read_text(encoding="utf-8").splitlines()[:80]
            if idx_lines:
                parts.append(
                    "[Memory Index Head]\n" + "\n".join(idx_lines)
                )
        except OSError:
            pass

    draft_block = _load_initial_draft_block(state)
    if draft_block:
        parts.append(draft_block)

    prior = _load_prior_round_summary(state)
    if prior:
        parts.append(prior)

    bucket_diff = _build_bucket_diff_block(state)
    if bucket_diff:
        parts.append(bucket_diff)

    if state.iteration > 1:
        parts.append(
            "Next step:\n"
            "本轮**首条回复**的 <intent> 必须先做递进式复盘（参考上面的 Recent rounds 与 Prior round）。\n"
            "**优先级约束**：若上方存在 [Teacher Review] 块，其『已饱和方向』为硬约束 —— "
            "即使上一轮分数有提升，也不得继续在已饱和方向上做参数微调；本轮 hypothesis 应"
            "落在 advice 的候选方向之一，或在复盘里显式给出可证伪的反驳证据后再否决某条 advice。"
            "在没有 advice 的轮次（仅 round 2 通常如此），若上轮方向有提升可适度延续，但仍要避免重复已失败的变体。\n"
            "请在复盘里给出下一轮可执行的具体意见，例如保留的机制、需要收紧/放宽的参数、"
            "需要重点观察的 bucket，以及避免重复的失败变体。\n"
            "**之后每一步仍按 OUTPUT_RULES 输出简短 <intent>（1-2 句中文，说明本步在做什么、为什么），"
            "但不要再重复 (a)(b)(c)(d) 复盘——recap 只在首条回复出现一次。**"
        )
    else:
        parts.append("Next step:")

    return "\n\n".join(parts)


def _load_initial_draft_block(state: RoundState) -> str:
    """Embed the round's starting draft.py only in the cold-start (round 1) header.

    For round 1, inlining saves an initial read_current_draft. For N>1, the
    draft is the incumbent — already known via Prior round / version_index;
    inlining it duplicates ~4-8KB per round. The agent should call
    read_current_draft on demand.
    """
    draft_path = state.run_dir / "draft.py"
    if not draft_path.exists():
        return ""
    if state.iteration > 1:
        try:
            line_count = sum(1 for _ in draft_path.open("r", encoding="utf-8", errors="replace"))
        except OSError:
            line_count = 0
        return (
            "Initial draft.py: seeded from incumbent "
            f"({line_count} lines; not inlined to save context). "
            "Call read_current_draft to view; use read_version for prior versions."
        )
    try:
        body = draft_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return (
        "Initial draft.py (seeded from incumbent; reflects round start only — "
        "after apply_patch, call read_current_draft to re-sync):\n"
        f"```python\n{body}\n```"
    )


def _build_bucket_diff_block(state: RoundState, *, window: int = 5) -> str:
    """Per-bucket stability snapshot vs the incumbent (best-so-far) over the
    last `window` scored versions in the current run.

    This surfaces "9/10 unchanged" patterns directly so the model does not have
    to recompute them via list_versions/read_version. Returns "" if there are
    fewer than 2 scored entries.
    """
    if state.iteration <= 1:
        return ""
    index_path = state.run_dir.parent.parent / "version_index.json"
    if not index_path.is_file():
        return ""
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    run_id = state.run_dir.name
    entries = [
        e for e in (data.get("entries") or [])
        if e.get("run_id") == run_id and isinstance(e.get("bucket_scores"), dict) and e["bucket_scores"]
    ]
    if len(entries) < 2:
        return ""
    recent = entries[-window:]

    incumbent = min(entries, key=lambda e: e.get("score", float("inf")))
    inc_buckets = incumbent.get("bucket_scores") or {}
    inc_v = incumbent.get("v")
    all_bucket_names = sorted(inc_buckets.keys())

    unchanged: list[str] = []
    movers: list[tuple[str, list[tuple[int, float]]]] = []
    for name in all_bucket_names:
        base = float(inc_buckets[name])
        deltas: list[tuple[int, float]] = []
        for e in recent:
            bs = e.get("bucket_scores") or {}
            if name not in bs:
                continue
            d = float(bs[name]) - base
            if abs(d) >= 0.005:
                deltas.append((int(e.get("v", 0)), d))
        if not deltas:
            unchanged.append(name)
        else:
            movers.append((name, deltas))

    lines = [
        f"Bucket stability (last {len(recent)} scored versions vs incumbent v{inc_v}):"
    ]
    if unchanged:
        lines.append(f"  unchanged ({len(unchanged)}/{len(all_bucket_names)}): " + ", ".join(unchanged))
    if movers:
        lines.append("  moved:")
        for name, deltas in movers:
            seg = ", ".join(f"v{v:03d}Δ={d:+.2f}" for v, d in deltas)
            lines.append(f"    {name}: {seg}")
    else:
        lines.append(
            "  → 全部桶在最近窗口内 Δ=0：当前假设类已饱和，下一假设必须改变机制类别（如 combo activation / chain reopt / classify 阈值），而不是再换一个排序键或备份上限。"
        )
    return "\n".join(lines)


def _load_prior_round_summary(state: RoundState) -> str:
    """Compact summary of the previous round's plan + report, for round header.

    Returns "" when no prior artifacts exist (round 1, or files missing).
    Failures are swallowed — this block is best-effort context only.
    """
    if state.iteration <= 1:
        return ""

    prev = state.iteration - 1
    blocks: list[str] = []

    harness_path = state.run_dir / f"harness_v{prev:03d}.json"
    if harness_path.exists():
        try:
            data = json.loads(harness_path.read_text(encoding="utf-8"))
            final = (data or {}).get("final") or {}
            plan = final.get("plan") or {}
            if plan:
                blocks.append(
                    f"Prior round v{prev:03d} plan:\n"
                    f"  hypothesis: {plan.get('hypothesis', '')}\n"
                    f"  analysis: {plan.get('analysis', '')}\n"
                    f"  target_buckets: {plan.get('target_buckets', [])}\n"
                    f"  edit_plan: {plan.get('edit_plan', [])}"
                )
        except (OSError, ValueError):
            pass

    report_path = state.run_dir / f"report_v{prev:03d}.txt"
    if report_path.exists():
        try:
            text = report_path.read_text(encoding="utf-8", errors="replace")
            blocks.append(f"Prior round v{prev:03d} report:\n{text}")
        except OSError:
            pass

    impact = _build_prior_round_impact_block(state, prev_iteration=prev)
    if impact:
        blocks.append(impact)

    return "\n\n".join(blocks)


def _match_bucket_keys(target: str, bucket_keys: list[str]) -> list[str]:
    """Match a target_bucket label (often a short form like 'scarce_seed401' or
    'scarce_couriers') against actual case names in bucket_scores. Falls back
    to substring + token-overlap so the model's varied wording still hits."""
    t = (target or "").strip().lower()
    if not t:
        return []
    # Exact match wins.
    for key in bucket_keys:
        if key.lower() == t:
            return [key]
    # Substring match (either direction).
    subs = [k for k in bucket_keys if t in k.lower() or k.lower() in t]
    if subs:
        return subs
    # Token-overlap: split on '_' and require ≥2 shared tokens (covers
    # 'scarce_seed401' ↔ 'scarce_couriers_seed401').
    t_tokens = {tok for tok in t.split("_") if tok}
    hits = []
    for key in bucket_keys:
        k_tokens = {tok for tok in key.lower().split("_") if tok}
        if len(t_tokens & k_tokens) >= 2:
            hits.append(key)
    return hits


def _build_prior_round_impact_block(
    state: RoundState, *, prev_iteration: int
) -> str:
    """Closed-loop summary: 'last round you aimed at X — here is X's actual Δ'.

    Reads version_index.json, finds the prev (just-finished) and prev-prev
    entries for the current run, and computes per-target-bucket Δ for the
    declared target_buckets in the prev plan. Also emits a full-bucket Δ
    one-liner so the model sees collateral movement in one glance.

    Returns "" when the data isn't available (early rounds, missing index,
    bucket_scores absent on either side).
    """
    index_path = state.run_dir.parent.parent / "version_index.json"
    if not index_path.is_file():
        return ""
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    run_id = state.run_dir.name
    entries = [
        e for e in (data.get("entries") or [])
        if e.get("run_id") == run_id
        and isinstance(e.get("bucket_scores"), dict)
        and e["bucket_scores"]
    ]
    if not entries:
        return ""
    entries.sort(key=lambda e: int(e.get("iteration", 0)))

    prev_entry = next(
        (e for e in entries if int(e.get("iteration", -1)) == prev_iteration), None
    )
    if prev_entry is None:
        return ""
    earlier = [e for e in entries if int(e.get("iteration", -1)) < prev_iteration]
    if not earlier:
        return ""
    base_entry = earlier[-1]  # the round immediately before prev

    prev_buckets: dict[str, float] = {
        k: float(v) for k, v in prev_entry["bucket_scores"].items()
    }
    base_buckets: dict[str, float] = {
        k: float(v) for k, v in (base_entry.get("bucket_scores") or {}).items()
    }
    incumbent = min(entries, key=lambda e: float(e.get("score", float("inf"))))
    inc_buckets: dict[str, float] = {
        k: float(v) for k, v in (incumbent.get("bucket_scores") or {}).items()
    }
    inc_v = incumbent.get("v")

    # Target buckets from the prev plan
    target_buckets: list[str] = []
    harness_path = state.run_dir / f"harness_v{prev_iteration:03d}.json"
    if harness_path.exists():
        try:
            hdata = json.loads(harness_path.read_text(encoding="utf-8"))
            plan = ((hdata or {}).get("final") or {}).get("plan") or {}
            tb = plan.get("target_buckets") or []
            if isinstance(tb, list):
                target_buckets = [str(x) for x in tb if x]
        except (OSError, ValueError):
            pass

    bucket_keys_sorted = sorted(prev_buckets.keys())
    lines: list[str] = [
        f"Prior round v{prev_entry.get('v')} bucket impact "
        f"(declared targets vs base v{base_entry.get('v')}; incumbent=v{inc_v}):"
    ]

    if target_buckets:
        target_lines: list[str] = []
        for tb in target_buckets:
            matched = _match_bucket_keys(tb, bucket_keys_sorted)
            if not matched:
                target_lines.append(f"  - {tb}: (no matching bucket in scoreboard)")
                continue
            for key in matched:
                p = prev_buckets.get(key)
                b = base_buckets.get(key)
                inc = inc_buckets.get(key)
                if p is None or b is None:
                    target_lines.append(f"  - {tb}→{key}: (insufficient data)")
                    continue
                d = p - b
                inc_d = "" if inc is None else f" | Δvs incumbent={p - inc:+.2f}"
                arrow = "↓" if d < 0 else ("↑" if d > 0 else "·")
                target_lines.append(
                    f"  - {tb}→{key}: {b:.2f}→{p:.2f} Δ={d:+.2f}{arrow}{inc_d}"
                )
        lines.append("targets:")
        lines.extend(target_lines)
    else:
        lines.append("targets: (prev plan declared no target_buckets)")

    # Full-bucket one-liner (collateral movement)
    one_liner_parts: list[str] = []
    for key in bucket_keys_sorted:
        p = prev_buckets.get(key)
        b = base_buckets.get(key)
        if p is None or b is None:
            continue
        d = p - b
        if abs(d) < 0.005:
            one_liner_parts.append(f"{key}=·")
        else:
            short = key.split("_")[0]  # e.g., 'scarce_couriers_seed401' → 'scarce'
            one_liner_parts.append(f"{short}:{d:+.2f}")
    if one_liner_parts:
        lines.append("all buckets Δ vs base: " + ", ".join(one_liner_parts))

    return "\n".join(lines)


def format_tool_user_message(
    *,
    name: str,
    args: dict[str, Any],
    ok: bool,
    content: str,
) -> str:
    """Render a single tool result as a standalone user turn."""
    args_str = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    status = "ok" if ok else "fail"
    return f"[tool_result name={name} status={status}] {args_str}\n{content}"
