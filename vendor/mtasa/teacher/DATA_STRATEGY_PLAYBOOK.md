# Teacher 数据与策略手册

## 策略建议

先制定测试计划再执行。

建议每轮只做一个可归因改动的测试。

### 难桶与起步顺序

- 起步阶段优先保证 `large/medium/small/high_noise` 的稳定覆盖与基础降分，形成可靠基线后再集中攻克 `scarce/low_w`。
  `scarce_couriers` 与 `low_willingness` 属于高难桶，通常需要更强场景化机制，早期盲目硬攻容易导致全局回退。
- 若 broad buckets 仍有明显改进空间，应先吃掉这些确定性收益，再投入高难桶探索。

## 目标函数

```python
  # 目标函数（罚分，越低越好）
  #
  # 输入：solver 返回的若干 (task_bundle, [courier1, courier2, ...]) 行
  # 输出：official_like_score  ——  total penalty
  #
  # 约定：每条 (task_bundle, courier) 候选都有 (score, willingness)。
  #       每一行的 couriers[0] 是主派，couriers[1:] 是备派（"额外通知"）。
  #       行内 task_bundle 含逗号 = 合单行（merged）。

  P_UNCOV_NORMAL = 100.0
  P_UNCOV_MERGED = 200.0
  PER_UNCOVERED_TASK_PENALTY = 100.0


  def tail_fold(pairs, p_uncov):
      """单向递归：从右往左折叠。
      经济含义：第 i 位骑手以 w_i 概率接单（得分 score_i），
      以 (1 - w_i) 概率拒单 → 沿用尾部期望 tail。
      最末端的 tail 起始值就是"没人接单"的罚分 p_uncov。
      """
      tail = p_uncov
      for score, willingness in reversed(pairs):
          tail = willingness * score + (1.0 - willingness) * tail
      return tail


  def row_penalty(selected_pairs, is_merged_bundle):
      """一行的罚分 = 对 willingness 升序、降序两种排列分别 fold，再取平均。
      双向平均消除了候选顺序敏感性。
      selected_pairs = [(score, w)] (主派) + [(score, w), ...] (备派，按
  couriers 顺序)
      """
      p_uncov = P_UNCOV_MERGED if is_merged_bundle else P_UNCOV_NORMAL
      asc  = sorted(selected_pairs, key=lambda sw: sw[1])           #
  willingness 升序
      desc = list(reversed(asc))                                     # 降序
      return 0.5 * (tail_fold(asc, p_uncov) + tail_fold(desc, p_uncov))


  def official_like_score(solution_rows, table, all_tasks):
      """
      solution_rows: [(task_bundle, [primary, backup1, backup2, ...]), ...]
      table:         {(task_bundle, courier_id): (tasks, score, willingness)}
                     —— 仅由 case 输入文件提供的合法候选才在 table 里
      all_tasks:     case 中所有原子任务集合（合单行的 bundle 已拆开）
      """
      visible_total = 0.0
      covered = set()
      used_tasks, used_couriers = set(), set()

      for task_bundle, couriers in solution_rows:
          primary = couriers[0]

          # 1) 合法性闸门：候选必须存在、任务/快递员未被占用、行内备派不重复。
          cand = table.get((task_bundle, primary))
          if cand is None:                       continue   # 非法行：直接丢弃
          tasks, score, w = cand
          if any(t in used_tasks for t in tasks): continue
          if primary in used_couriers:           continue

          selected_pairs = [(score, w)]
          row_couriers = {primary}
          ok = True
          for backup in couriers[1:]:
              if backup in row_couriers or backup in used_couriers:
                  ok = False; break
              bcand = table.get((task_bundle, backup))
              if bcand is None:
                  ok = False; break
              _, bscore, bw = bcand
              selected_pairs.append((bscore, bw))
              row_couriers.add(backup)
          if not ok:
              continue

          # 2) 行内罚分（双向 fold 平均），按合单/普通选 p_uncov。
          is_merged = ("," in task_bundle)
          visible_total += row_penalty(selected_pairs, is_merged)

          used_couriers |= row_couriers
          used_tasks    |= set(tasks)
          covered       |= set(tasks)

      # 3) 总罚分 = 已选行的罚分之和 + 100 × 未覆盖任务数
      uncovered = len(all_tasks - covered)
      return visible_total + PER_UNCOVERED_TASK_PENALTY * uncovered
```



## 分类型触发规则（运行时判定）

需要分类型单独优化时，可以使用策略代码

#### 简单版本

```python
def _classify(feat):
    if feat["courier_ratio"] < 0.7:
        return "scarce"
    if feat["avg_w"] < 0.22:
        return "low_willingness"
    if feat["score_cv"] > 0.5:
        return "high_noise"
    return "normal"
```

**判断序**（互斥）：

scarce → low_willingness → high_noise → normal

**用到的特征**：

- `courier_ratio = n_couriers / n_tasks`
- `avg_w` = 候选集 willingness 均值
- `score_cv` = score 变异系数



#### 详细版本

制作一个可被正式 solver 复用的 10 桶分类器，并在线上确认以下细分：

```text
tiny, small,
medium201, medium202, medium203,
low_willingness, high_noise,
scarce, large301, large302
```

分类特征全部从当前线上输入内部计算，不使用本地 `official/large_seed301.txt`
作为 row 或分数对照。

##### 分类规则

粗分类：

| bucket            | 规则                                     |
| ----------------- | ---------------------------------------- |
| `tiny`            | `n_tasks == 6`                           |
| `small`           | `n_tasks == 15`                          |
| `low_willingness` | `n_tasks == 30 and avg_w < 0.22`         |
| `high_noise`      | `n_tasks == 30 and std_score >= 20.0`    |
| `scarce`          | `n_tasks == 40 and courier_ratio < 0.70` |

细分类：

| bucket      | 规则                                                  | 来源      |
| ----------- | ----------------------------------------------------- | --------- |
| `medium201` | `best_solo_delta_per_task_mean >= 76.7`               | probe #25 |
| `medium202` | 其余 medium 且 `combo_vs_solo_synergy_std < 10.67`    | probe #28 |
| `medium203` | 其余 medium                                           | probe #28 |
| `large302`  | 非 scarce large 且 `second_solo_delta_ratio >= 0.967` | probe #26 |
| `large301`  | 其余非 scarce large                                   | probe #26 |

