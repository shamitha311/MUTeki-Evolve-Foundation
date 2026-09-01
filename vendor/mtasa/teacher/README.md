# Teacher 模块（精简版）

Teacher 是 Fool 的经验与护栏模块，不负责打分，也不直接产出最终 solver。

当前目标不是“多文档堆叠”，而是把对优化真正有用的信息压缩成少量高价值文档，避免泛泛而谈。

## 当前仅保留 3 个文档

1. `EXPERIMENT_REVIEW_CHECKLIST.md`
2. `DATA_STRATEGY_PLAYBOOK.md`
3. `README.md`（本文件）

## Fool 的强制读取规则

每轮都必须读 `EXPERIMENT_REVIEW_CHECKLIST.md` 三次：

1. 改代码前。
2. 出结果后。
3. 准备保留 best 版本前。

## 核心原则

1. 所有建议必须有数据依据（至少 case 级分数/覆盖/未覆盖之一）。
2. scarce 不做概念化描述，直接按“当前可执行策略 + 数值阈值”说明。
3. 出现回归时先回到证据，不允许靠印象切换策略。

## 评测认知边界

1. 当前主流程按 official_like 计算：总分 = visible_total + 100 * uncovered_tasks。
2. ordinary + backup-only 行为可用于稳定迭代。
3. merge-bundle 仍有不确定区间，需通过 A/B 和 case 级证据验证。
