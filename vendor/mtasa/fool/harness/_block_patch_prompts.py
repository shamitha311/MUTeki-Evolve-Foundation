"""Prompt fragments for the block_patch tool.

Composition convention: any tool whose usage needs more than a one-line
``ToolSpec.description`` gets a ``_<tool_name>_prompts.py`` module exposing
``SECTION`` — a self-contained block that ``prompt.build_prefix`` appends to
the system prefix. Keep tool-specific prose **out** of ``prompt._OUTPUT_RULES``.
"""

from __future__ import annotations


RULES = """\
block_patch 行为说明（SEARCH/REPLACE 风格，无行前缀，作用于 draft.py）：

1) **基于当前 draft**：作用于 draft.py 的当前内容。若前一步刚做过编辑、不确定 draft 现状，先调 read_current_draft。

2) **SEARCH 必须是 draft.py 中一段连续切片**：逐字符复制——缩进、空行、注释、引号、空格都要一致。

3) **拆小块、写够上下文（最关键的稳健性规则）**：
   - 每个 SEARCH 只覆盖**真正改动的若干行 + 0–3 行用于消歧的相邻上下文**。
   - **单个 SEARCH block 不要超过 30 行**——超过就拆成多块；跨函数的改动必须拆，不要试图用一个大 SEARCH 包整段重写整个函数甚至多个函数。
   - **不要**把整段未改动的函数体塞进 SEARCH——块越大、匹配失败的概率越高、诊断越难定位。
   - 改 N 处独立的地方就开 N 个 SEARCH/REPLACE 块（一个 <blocks> 里塞多块即可）。
   - SEARCH 必须能在 draft 中**唯一**匹配；若旧片段在文件里出现多次，把 SEARCH 往外扩 1–3 行相邻代码直到唯一。

4) **第一处命中**：若 SEARCH 在 draft 中仍多处匹配，匹配器取第一处。务必通过 (3) 把 SEARCH 写到唯一，不要依赖位置碰运气。

5) **空 SEARCH = 追加到文件末尾**（常用于新增 helper 函数）。

6) **多块原子**：一个 <blocks> 里可以塞多个 SEARCH/REPLACE 对，按出现顺序施加；**全部成功才写盘**，任一失败整批回滚，draft 保持原样。

7) **搬动代码 = 2 个块**：1 个块把旧位置删掉（SEARCH=旧代码，REPLACE 为空），1 个块在新位置插入（SEARCH=锚点行，REPLACE=锚点行 + 旧代码）。不要试图用 1 个块同时改两处。

8) **REPLACE 按字面写入**：缩进、空行都按你写的写入；匹配器**不会**替你推断缩进。

9) **不要写 diff 前缀**：block_patch 不是 unified diff、也不是 V4A —— SEARCH 和 REPLACE 内**不要**加 `+`、`-`、单空格、`@@`、`*** Begin/End Patch` 等前缀，直接写原文。

10) **匹配兜底——不要依赖**：若 SEARCH 整体比 draft 少（或多）一段统一前导空格，匹配器会尝试整体平移对齐；若 SEARCH 开头多 1 行空行也会被忽略。这是失败兜底，**不是**写得马虎的授权——能逐字符复制就逐字符复制，缩进改错被静默通过比失败更糟。

11) **失败诊断**：失败时会回传"哪个块没匹配 + draft 中最相近的一段切片"。看到失败时先 read_current_draft 把目标行原样复制进 SEARCH，再**只重发**失败的那一块。

快照（由 harness 自动管理）：每次 block_patch 成功都会自动把"打补丁前"的 draft 存为 vNNN 快照（v001 = 第 1 次 patch 之前，v002 = 第 2 次之前，依此类推）；成功返回里会写明刚刚保存的 label。你不能也不需要主动 snapshot——它已从工具表移除。若某次 patch 后实验失败、想回到打补丁前状态，直接调 restore_draft(label="vN")；也可以连续 restore 跳更早版本。
"""


EXAMPLES = """\
block_patch 示例（仅供格式参考；下面代码只是格式示范，draft.py 并不一定有这些行）：

【示例 1】改一处常量——典型的最小块：
<tool name="block_patch"><blocks>
<<<<<<< SEARCH
    MAX_ITERS = 50
=======
    MAX_ITERS = 200
>>>>>>> REPLACE
</blocks></tool>

【示例 2】改一段函数体——SEARCH 只包含该函数本身，不连带前后无关代码：
<tool name="block_patch"><blocks>
<<<<<<< SEARCH
def _score(courier, bundle):
    return courier.total_score * len(bundle)
=======
def _score(courier, bundle):
    base = courier.total_score
    return base * len(bundle) * courier.willingness
>>>>>>> REPLACE
</blocks></tool>

【示例 3】同一轮里改两处独立的参数——开 2 个独立的小块，**不要**合成 1 个大块：
<tool name="block_patch"><blocks>
<<<<<<< SEARCH
TIME_WEIGHT = 0.4
=======
TIME_WEIGHT = 0.6
>>>>>>> REPLACE
<<<<<<< SEARCH
WILL_WEIGHT = 0.6
=======
WILL_WEIGHT = 0.4
>>>>>>> REPLACE
</blocks></tool>

【示例 4】在文件末尾追加新 helper——空 SEARCH 段：
<tool name="block_patch"><blocks>
<<<<<<< SEARCH
=======


def _bundle_size(task_id_list):
    return len(task_id_list.split(","))
>>>>>>> REPLACE
</blocks></tool>

【反例 ✗】把整段未改动的函数体塞进 SEARCH 只为改 1 行——块越大越脆，失败概率成倍上升。永远只覆盖真正改动的几行 + 必要的消歧上下文。

【反例 ✗✗ 形态错误（会被解析器直接拒绝）】把 blocks 塞进 <args> JSON：
<tool name="block_patch"><args>{"blocks": "<<<<<<< SEARCH\\n..."}</args></tool>
SEARCH/REPLACE 里的换行/双引号/反斜杠必然破坏 JSON 转义，解析失败浪费一个 round。
block_patch 的 **唯一合法形态** 是 <blocks>...</blocks> 裸文本子标签，**永远不要** 用 <args> 包裹。
"""


SECTION = "\n\n".join([RULES.strip(), EXAMPLES.strip()])
