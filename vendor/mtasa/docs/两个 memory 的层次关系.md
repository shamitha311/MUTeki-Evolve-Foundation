两个 memory 的层次关系



  三层存储

  层: A. 单轮转写
  路径: out/runs/<run_id>/dialog/round_NNN.jsonl、tool_results/<uuid>.txt
  写入者: dialog_writer.py
  生命期: 一轮一文件；不参与下轮上下文
  检索方式: 不被自动检索；agent 通过 read_tool_result 续读截断的工具输出
  ────────────────────────────────────────
  层: B. 数据集级
  路径: out/memory/runs/<dataset_fp>/ – episodes.jsonl (评分回放) –
    strategy_index.json (压缩策略索引) – session_summaries.jsonl (压缩摘要) –
    buckets/<bucket>/champion.py + meta.json
  写入者: FoolMemory (fool/memory_store.py)，BucketIncumbents
    (fool/bucket_incumbents.py)
  生命期: 同一指纹数据集间累积；切数据集后只读 fp 下的文件
  检索方式: BM25，按 fp 隔离
  ────────────────────────────────────────
  层: C. 全局长期
  路径: out/memory/MEMORY.md + out/memory/notes/*.md (+
  notes/datasets/<fp>_*.md)
  写入者: MemoryNotesStore (fool/memory_notes.py)
  生命期: 跨数据集累积；只有"清空全局记忆"按钮会动
  检索方式: BM25，全局

  buckets/<bucket>/champion.py 是 per-dataset 的"桶冠军 solver"，不是
  markdown，不是给 LLM 读的文本，是给 fool_loop 用来记分基线的。

  写入路径

  写入 B（数据集级）：
  - durable_memory.record(...) — fool_loop.py:1221, 1375 每轮提交完写入
  episodes.jsonl + 刷新 strategy_index.json
  - durable_memory.bucket_incumbents.record(...) — fool_loop.py:1332
  桶级改进时替换 champion
  - durable_memory.store_best_if_better(...) — fool_loop.py:697, 1355
  全局最优时镜像到 best_*
  - memory.record_session_summary(...) —
  压缩回调（_make_session_summary_callback @
    fool_loop.py:49），把压缩出来的摘要追加到 session_summaries.jsonl

  写入 C（全局长期）：四条通路

  1. LLM 显式 memory_write 工具 — fool/harness/tools.py:571，agent
  在每轮内可主动写
  2. outcome_reflector — fool/harness/outcome_reflector.py:541，每轮跑完按
  outcome 让 LLM 反思，自动选 section 写
  3. session_compactor 关键决策路由 —
  fool/harness/session_compactor.py:388，压缩摘要里 ## 关键决策
    段自动拆条目，每条 → notes/key_decision_*.md
  4. stagnation decay — fool_loop.py:1458，连续 3 轮停滞时给相关 try_error 的
  confidence ×0.5（rewrite frontmatter，不删除）

  每条写入完成后 aggregate_index() 在循环尾部跑一次（fool_loop.py:1559），跑完整
   run 后再兜底跑一次（fool_loop.py:1564）—— 这是上一轮你修的那个 bug 的修法。

  检索路径（"使用机制"）

  B 在哪被读：
  - 进入每轮 prompt 的 Prior round vNNN block 和"recent history"段 —— runner.py
  里通过 durable_memory 拼到 user header
  - 工具 retrieve_guidance 同时检索 B 的 episodes.jsonl 和 teacher/ 静态文档

  C 在哪被读：
  - 工具 memory_search / memory_get（tools.py:585+）—— agent 主动查
  - outcome_reflector 写入前会先 _search_similar()
  找相邻历史（outcome_reflector.py:267），避免重复写
  - 进 prompt 的不是 markdown 全文，而是 MEMORY.md 索引行 + 按 query 取出的
  snippet

  一句话总结流向

  ▎ 每轮产出 → 评分 → B 永久记录（episode + champion）→ C 由 LLM
  ▎ 反思决定是否提炼跨数据集 lesson；中间 dialog 是 A，只是断点恢复用。

  B 是"这个数据集上发生过什么"，C 是"任何数据集都该记住的教训"。B 严格按 fp
  隔离，所以换数据集等于从零开始（除了能从 C 借经验）。