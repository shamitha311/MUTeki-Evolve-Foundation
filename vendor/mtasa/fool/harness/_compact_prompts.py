"""Chinese prompts for harness transcript compaction.

Ported from ReMe (reme/memory/file_based/components/compactor.yaml,
_zh variants). MTASA-specific edits: replaced the generic "对话" wording
with "Fool harness 一轮内的多轮工具调用记录" so the LLM understands the
domain.
"""

SYSTEM_PROMPT_ZH = """你是一个上下文压缩助手。你的角色是为 Fool harness 一轮内的多轮工具调用记录创建结构化摘要，
这些摘要可以在未来轮次中用于恢复上下文。专注于保留关键信息，同时减少 token 数量。
保留：solver 当前的设计假设、已尝试的修改、Genius 评分反馈、被否决的方向与原因。
丢弃：完整 solver 源码、完整 Genius 报告原文（保留关键数字即可）、重复的 retry 提示。"""


INITIAL_USER_MESSAGE_ZH = """# 任务
根据上面的对话创建一个结构化摘要。

# 规则
- 保持每个部分简洁
- 保留确切的文件路径、函数名称和错误消息
- 保留 Genius 报告中的关键数字（total_score、coverage、uncovered_tasks 等）

# 输出格式

## 目标
[本轮要验证的核心假设是什么]

## 约束和偏好
- [用户/teacher playbook 提到的约束、偏好或要求]
- [或者如果没有提到则为"(none)"]

## 进展
### 已完成
- [x] [已完成的修改、已运行的工具]

### 进行中
- [ ] [当前工作]

### 阻塞
- [如果有任何阻碍进展的问题]

## 关键决策
- **[决策]**: [简短理由]

## 下一步
1. [接下来应该发生的事情的有序列表]

## 关键上下文
- [继续工作所需的数据、示例或参考]
- [或者如果不适用则为"(none)"]

按上述格式输出结构化摘要。"""


UPDATE_USER_MESSAGE_ZH = """# 任务
基于上面的对话和上一轮 previous-summary，更新结构化摘要。

# 规则
- 保留上次摘要中仍然适用的内容
- 把新的进展、决策、阻塞合并进来
- 移除已经被新证据推翻的旧假设
- 保留所有 Genius 评分关键数字

# 输出格式
与首次摘要相同（## 目标 / ## 约束和偏好 / ## 进展 / ## 关键决策 / ## 下一步 / ## 关键上下文）。"""
