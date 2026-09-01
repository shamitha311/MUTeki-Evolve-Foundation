# 美团 Track4 算法优化参考手册 V2

> V2 在 V1 的"结构性结论"基础上，吸收了另一支团队（MTASA）经线上 A/B 验证的**参数化与桶隔离**经验，并补充工程纪律（提交前 checklist、回退红旗）。
> **核心心智**：分数改进 80% 来自结构选择，剩余 20% 来自**按桶隔离的参数化** —— 绝不做全局参数微调。

---

## 0. 平台 / 沙箱硬约束（先满足，否则全 case 0 分）

### 0.1 沙箱可用的库

线上沙箱**只能用 Python stdlib**。已实测不可用：

| 库                                             | 状态                         |
| ---------------------------------------------- | ---------------------------- |
| `ortools` / `pulp` / `scipy+numpy` 联合 / `os` | ❌ ImportError，全 case error |

可用：`time`, `random`, `collections`, `math`, `heapq`, `bisect`, `itertools`。

**提交前自检**：`grep "^import\|^from" solver_xxx.py`，应只剩这几个。任何"函数内部 import ortools 做 fallback"在线上一定走 fallback ——历史上号称 CP-SAT 的版本线上跑的都是贪心。

### 0.2 源代码字面量陷阱

源代码里**不要写 list-of-tuple-of-list 字面量 >10 行**（典型 `[(t, [c]), ...]`），平台前端 JS 解析抛 `Cannot read properties of undefined (reading 'slice')`，**全 case 0 分**。改为"单字符串常量 + runtime split" 立刻正常（实测 943.79）。

**自检**：`grep -n "\[(" solver_xxx.py | grep -v "import\|return\|def"` 应为空。

注意：`solve()` 返回值 `[(bundle, [courier]), ...]` 是接口规定，必须保持。

### 0.3 时间预算悬崖

- 线上 时间预算约为 30s**，单 case timeout = **4000 罚分**。





---

## 1. 问题的真实结构（先掌握这几条，能省 90% 弯路）

### 1.1 Bundle 大小线上 max=2

线上 10 个 case 全部 `max_bundle_size == 2`，不存在 n≥3 合单。

→ 任何 K=3、K=4、n=3 合单的设计**完全无用**。

### 1.2 平台打分公式（partner_f，越低越好）

按 bundle 计算后求和，再加 `100 × uncov_tasks`：

- **单通知** (1 courier)：`w·s + (1-w)·100·n`

- **多通知** (m ≥ 2 couriers)：`0.5 · (recur_desc + recur_asc)`

  ```
  recur(s_1..s_m, w_1..w_m, n) = w_1·s_1 + (1-w_1) · recur(s_2..s_m, w_2..w_m, n)
  base m=0: 100·n
  desc = 按 w 降序;  asc = 按 w 升序
  ```

→ 任何 solver 的**内部 selector 必须用这个公式**才能与线上对齐。旧"只 desc"或"Formula-A 单步加权"会系统偏差。

### 1.3 Combo 在线上呈"负协同"分布

同骑手 combo `(T1, T2, C)` 的 synergy = `delta_combo - delta_solo_T1 - delta_solo_T2`：

| case 类型                      | mean  | std     | P(synergy > -10) |
| ------------------------------ | ----- | ------- | ---------------- |
| 普通 / large / scarce / medium | ≈ −35 | ≈ 11    | < 3%             |
| high_noise                     | ≈ −35 | ≈ 12-13 | < 3%             |
| low_willingness                | ≈ −23 | ≈ 6     | ≈ 5%             |
| tiny / small                   | ≈ −35 | ≈ 6     | < 5%             |

含义：随机吃一个 combo，97% 概率比拆 2 solo 多亏 ~35 分。

→ 不全量、不全删。**per-candidate synergy 过滤**（阈值 ≈ -10），是 ~790 → ~750 量级的最大单点 ROI。

### 1.4 v9 / lns_v47 在 large 上的真实结构

不是"很多 solo + 少量 combo"，而是 **35/40 是 2-courier multi-notify，5/40 solo，0 combo**；medium 类似；scarce 反过来（0 multi-notify、18/22 combo）。

→ "基于 solo 找 combo 升级" 的 chain reopt MVP 在 large/medium 上**没东西可咬**。要让它有空间，operator 集合必须含 **"demote multi-notify → 释放骑手 → 给 combo 用"**。

### 1.5 下界与剩余空间

- `large_seed301` multi-notify k=2 真·下界 = **606.33**，v9 = 671.68 → **+65**。
- 65 分主要是"骑手冲突解决成本"（unconstrained pick 中 27/62 骑手被重用 5× 严重）。
- 推 v9 → 640 的方向是 **multi-notify pair joint optimization**（min-cost b-matching / Lagrangian），不是逐任务贪心 + LNS swap。

---

## 2. 已验证有效的方法（按 ROI 排）

### 2.1 Per-candidate combo synergy 过滤（最大 ROI）

见 §1.3。anchor-greedy 范式 ~790 → ~750。

### 2.2 Destroy-rebuild LNS（基础范式升级）

本仓库演化：`v69 anchor-greedy 768.86 → lns_v1 754.95 (-14) → lns_v2_med 727.68 → lns_v9 725.74 → lns_v47 721.13`。

**最小可行 LNS**：

1. 取当前 best 解作 incumbent。
2. 重复 N 轮：
   - **destroy**：按 row penalty 降序选前 5-10% 行 unpick。
   - **rebuild**：剩余候选池按 anchor A（formula 密度）greedy 重选。
   - 接受 if score 改善（hill-climb 即可，不需要 SA）。
3. 多 random tie-break 起点（5-8 次），取最优。

### 2.3 多策略并行（同预算内跑多邻域，非延长单次）⭐ MTASA 验证

另一支团队的线上 A/B：在 low_w 桶里**同预算**跑 `top_k=16` 和 `top_k=20` 两个 LNS 实例并取最优，改进 -1.21（low_w 桶内）。

**机制**：更广的 `top20` 候选池找到与 `top16` 不同的局部最优；同时跑、用同一个 selector 选最优，捕获邻域多样性而**不增加总时间**。

**注意（与本仓库经验对齐）**：

- ❌ 不要把单次 LNS 时间从 2s 延到 4s —— 温度计划改了反而局部回退（本仓库 v5 也证伪过：1801.43 → 1801.65 噪声）。
- ✅ 要跑两个独立 strategy，**预算各占原 1/N**（不要总时间膨胀，否则触发 §0.3 timeout 悬崖）。
- ✅ 两个 strategy 必须共享同一个 selector（用 §1.2 公式打分）。
- 邻域差异可来自：`pool_top` 不同、`K` 不同、random seed 不同、起点 anchor 不同。

### 2.4 2-task chain re-optimization

作为 LNS 后微改进（零风险，<0.1s/case）。4 个 operator：`change_subset`、`combo_split`、`pair_task_swap`、`solo_pair_merge`。Calibrated 上 7/10 case 改进累计 -40；线上首次提交 -0.74。

**正确性约束**：每个 candidate state 用 multi-notify 公式 `0.5·(recur_desc + recur_asc)`，不能假设 single-notify。

### 2.5 删冗余 anchor

6 anchor 第 4 个之后边际为 0，常见可删 E、F（coverage-first，线上无 n≥3）。省下的 CPU 全给 LNS 迭代。

### 2.6 按 w 自适应的 backup（multi-notify）

- low w 行（w < 0.2）：backup 上限 3-4 个仍有收益。
- 中高 w：max 2 即可。
- backup 必须**完全相同 raw_bundle**（scorer 按字符串查表）。

---

## 3. 按桶参数化（middle ROI，低风险）⭐ MTASA 主要贡献

**核心纪律**：**任何参数变更必须有"狭窄特征门控"，绝不全局应用**。一个全局变更回退多个桶 → 立刻撤回（§5.1 红旗）。

### 3.1 桶识别 → 参数响应曲线 → 狭窄门控

流程：

1. **提取案例特征**：`n_tasks`、`score_cv`、`avg_w`、`courier_ratio` 等。
2. **画参数响应曲线**：固定种子集 [7,13,17,37,59,83] 跑 5-7 个参数值，看每个桶的分数曲线。
3. **找非单调最优**：很多参数不是"越大越好"，例如 scarce 的 fail_weight = 1.0 最优、0.85 次之、0.60 最差。
4. **设置狭窄门控**：用多维特征（不只 `n_tasks`），例如 `n_tasks == 30 and score_cv <= 0.352`，仅在精确匹配时切换参数。
5. **种子化 A/B 验证**：10 case × 6 seed，确认不回退其他桶。

### 3.2 已知敏感参数（来自 MTASA 线上 A/B）

| 桶                           | 参数                    | 调整       | 桶内改进                        | 其他桶副作用          |
| ---------------------------- | ----------------------- | ---------- | ------------------------------- | --------------------- |
| scarce                       | `fail_weight`           | 0.85 → 1.0 | **-133**                        | 0                     |
| medium202 (score_cv ≤ 0.352) | clean-backup `min_gain` | 3.0 → 2.0  | +68 ~ +0.93                     | 0（其他 medium 不变） |
| 非 low/scarce/high_noise     | `min_gain`              | 3.0 → 2.5  | large302 +18.82, large301 +3.09 | medium201 -2.41 ⚠️     |

→ 第三行是**反例**：阈值"全局降"会回退 medium201。MTASA 的经验是：**这种小副作用就不要提交**，等找到更窄的门控（v23 用 `score_cv ≤ 0.352` 包到只命中 medium202）。

### 3.3 反面模式

- ❌ 全局调参数（即便平均分赢）
- ❌ 离线没固定种子序列就直接改参数提交
- ❌ 在某桶找到甜点后继续微调 —— MTASA v48-v52 在 v47 scarce 成功后试了 9 个变体**全部回退**。**锁定，换下一个桶**。
- ❌ 单 case 改善不算数 —— 必须 10 case × 6 seed 看分布。

### 3.4 反复在 low_w 上微调是低 ROI

low_w 难度纯来自数据（w ≈ Beta(3, 55) 集中 0.05），avg cost ≈ 190 是物理下界。

突破必须**操作 w 维度**（自适应权重 / 主动放弃低 w 任务），不是参数微调。MTASA 也试过桶专用 selector（visible-coverage 优先 + platform-score 打破平局），离线赢线上没维持，搁置。

---

## 4. 已证伪的死路（别再走）

| 方向                                  | 证伪方式                                 | 结论                                                   |
| ------------------------------------- | ---------------------------------------- | ------------------------------------------------------ |
| 堆 LNS 时间（2s → 5s）                | low_w 1801.43 → 1801.65                  | 固定 POOL/K 下已贴下界                                 |
| 单纯 Warm-start LNS                   | low_w 1801.97（+0.54）+ 多花 4.2s        | 起点与原解同盆地                                       |
| Multi-restart LNS                     | low_w 1800.57（-0.86 噪声）              | 同上                                                   |
| 扩 LNS 列池 POOL=20 K=4               | greedy 覆盖率 30/30 → 22/30，1801 → 1961 | 列池超 100 万要警惕，超千万 SA 一次都接受不了          |
| K=3 / K=4 全枚举                      | 同上                                     | 想 K≥3 必须 selective（只扩高成本 bundle）+ warm-start |
| "2 solo → combo" 局部 swap            | 官方 large_seed301 上 0 combo 不变       | uncov 罚分 200 > 2 solo 100+100，SA 拒绝               |
| 暴力枚举全 30580 combo 单步激活       | 在 v9 上**最优 delta = +0.00**           | v9 是单步 combo activation 严格局部最优                |
| 任何"combo 偏置"启发式                | 多次失败                                 | 不值得再试                                             |
| 在 low_w 上做"通用"算法改进           | 数据 w 集中 0.05                         | 物理下界                                               |
| Formula-A 单步加权作目标函数          | 与多通知公式不匹配                       | 用 §1.2                                                |
| 全局降阈值（min_gain 3.0→2.5 全应用） | medium201 回退 -2.41                     | 用 §3.2 狭窄门控                                       |
| 在桶甜点上继续微调                    | MTASA v48-v52 测 9 变体全回退            | 锁定换下一桶                                           |

---

## 5. 测试方法学

### 5.1 红旗（看到立刻停手）

- 🚩 **一个改动回退多个桶** → 可能是全局参数级联，必有更窄门控
- 🚩 **离线赢线上没维持** → 过拟合本地伪数据
- 🚩 **种子化 A/B 平均赢，但 high_noise 或 large 回退** → 可能是时序 / runtime 交互（如 §0.3 timeout 边缘）
- 🚩 **参数扫描呈非单调** → 可能过度约束或特征交互；先理解再调

### 5.2 不要直接调内部函数做 forced test

`_lns_candidate(warm_start=...)` 直接调测的"改善"，跟线上 `_classify()` 路由后的真实路径不一致。曾误判 warm-start 有效（forced -24.96，线上 +0.54）。

### 5.3 同档 (same-tier) 比较不要靠本地 sim

本地校准 sim 跨档保序，**同档比较经常反向**（sim +18 实际 -4.6）。绝对值差 **<30/case** 都算噪声；多 seed × 多 case 看分布。

### 5.4 平台是 deterministic 的

同一份 solver 跨提交分数完全相同（D-str 749.94/1576.36 精确复现）。分数变了**真的是改动效果**（除 timeout）。

---

## 6. 提交前 checklist

- [ ] `python -m py_compile solver_xxx.py` 通过
- [ ] `grep "^import\|^from"` 只有 stdlib（§0.1）
- [ ] `grep -n "\[(" solver_xxx.py | grep -v "import\|return\|def"` 为空（§0.2）
- [ ] **隔离审计**：`grep` 确认改动只影响目标桶 / 路径
- [ ] **离线 screen**：5+ 伪 case 上固定种子比对 v_new vs v_old
- [ ] **种子化 A/B**：固定种子集 [7,13,17,37,59,83] × 10 case
- [ ] 无覆盖回退：所有 case ≥95% 最大理论覆盖
- [ ] runtime 在预算内：无 case 超 9.0s wall clock（§0.3）
- [ ] 关键 large/medium 路径改动 → **两次提交**确认稳定性（防 CPU 抖动误判）

---

## 7. 按分数段的入口表

| 当前分    | 主要瓶颈                           | 第一步做什么                                                |
| --------- | ---------------------------------- | ----------------------------------------------------------- |
| > 1000    | 大概率 timeout 或 error            | 检查 §0.1 / §0.2 / §0.3                                     |
| 850 - 950 | 用了 Formula-A 旧公式 / 全量 combo | §1.2 公式 + §2.1 synergy 过滤                               |
| 750 - 850 | anchor-greedy 范式天花板           | §2.1 + §2.2 destroy-rebuild LNS                             |
| 725 - 750 | LNS 单步 swap 饱和                 | §2.3 多策略并行 + §2.4 chain reopt + §1.4 多 demote 算子    |
| 722 - 728 | 桶参数未调                         | §3 按桶参数化（scarce fail_weight 单点可省 ~13/avg）        |
| < 722     | 已贴 multi-notify LB               | §1.5 multi-notify joint optimization（min-cost b-matching） |

---

## 8. 优化心法（一图概括）

```
分数空间                  对应手段
─────────────────────────────────────────────────
> 1000  ←─ 工程错误     ─→  §0 沙箱/字面量/timeout
 800s   ←─ 算法范式低   ─→  §1.2 公式 + §2.1 synergy
 750s   ←─ greedy 天花板 ─→  §2.2 destroy-rebuild LNS
 730s   ←─ 单步 swap 饱和 ─→  §2.3 多策略并行 + §2.4 chain reopt
 725s   ←─ 桶未隔离     ─→  §3 按桶参数化（窄门控）
 < 722  ←─ 算法天花板   ─→  §1.5 joint optimization
 700    ←─ 目前公开赛第一名的成绩
```

> 一句话：**先做结构（§1-2），再做隔离（§3），最后做调参（§3.2）；任何全局微调都是错的**。

