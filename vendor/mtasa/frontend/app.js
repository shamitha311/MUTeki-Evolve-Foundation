const el = (id) => document.getElementById(id);
const FIXED_SCORING_MODE = "official_like_latest";
const BAD_WORD_RE = /(fail|error|timeout|invalid|not connected|missing|refus)/i;
const STAGE_LABELS = {
  idle: "空闲",
  finished: "已完成",
  stopped: "已停止",
  stopping: "停止中（等待当前操作结束）",
  error: "错误",
  "checking ai": "检查 API",
  "reading teacher": "读取老师建议",
  "planning solver": "规划本轮方案",
  "loading bootstrap": "加载初始算法",
  "precheck large301": "预检 large_seed301",
  "generating solver": "生成算法",
  judging: "提交评分",
  revising: "保留最佳算法",
};
let _manualApiExpanded = false;
let _automaticApiProfile = "";
const DEFAULT_EFFORT_LEVEL = "low";
const PROVIDER_EFFORT_OPTIONS = {
  "openrouter": ["low", "medium", "high", "xhigh"],
  "aliyun": ["low", "medium", "high", "xhigh"],
  "deepseek": ["low", "medium", "high", "xhigh"],
  "default": ["low", "high"],
};

function setText(node, value) {
  if (!node) return;
  const v = value == null ? "" : String(value);
  if (node.textContent !== v) node.textContent = v;
}

function setSignal(id, text, tone = "neutral") {
  const node = el(id);
  setText(node, text);
  const cls = `signal-${tone}`;
  if (!node.classList.contains(cls)) {
    node.classList.remove("signal-ok", "signal-bad", "signal-warn", "signal-neutral");
    node.classList.add("signal", cls);
  }
}

function stageTone(status) {
  const stage = String(status.stage || "").toLowerCase();
  if (stage === "error") return "bad";
  if (stage === "idle") return "bad";
  if (stage === "stopped") return "bad";
  if (stage === "stopping") return "warn";
  if (stage === "finished") return "ok";
  if (Boolean(status.running)) return "ok";
  if (stage.includes("checking") || stage.includes("generating") || stage.includes("judging") || stage.includes("reading") || stage.includes("revising")) {
    return "ok";
  }
  return "warn";
}

function stageLabel(stageText) {
  const normalized = String(stageText || "idle").toLowerCase();
  if (STAGE_LABELS[normalized]) return STAGE_LABELS[normalized];
  const waiting = normalized.match(/^waiting for manual score \(iter (\d+)\)$/);
  if (waiting) return `等待手动评分（第 ${waiting[1]} 轮）`;
  const approval = normalized.match(/^waiting for iteration approval \(iter (\d+)\)$/);
  if (approval) return `等待接受第 ${approval[1]} 轮计划`;
  return String(stageText || "idle");
}

function renderErrorBanner(status) {
  const node = el("errorBanner");
  const msg = String(status.last_error || "").trim();
  const isError = msg.length > 0;

  if (isError) {
    node.textContent = `失败提示：${msg}`;
    node.classList.remove("hidden");
    return;
  }

  if (String(status.stage || "").toLowerCase() === "error") {
    node.textContent = "失败提示：运行失败，请检查 API 设置与日志。";
    node.classList.remove("hidden");
    return;
  }

  node.textContent = "";
  node.classList.add("hidden");
}

async function postJson(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  return res.json();
}

function gatherConfig() {
  const selectedProfile = el("apiProfile").value || "manual";
  const config = {
    api_profile: selectedProfile,
    model: el("model").value,
    iterations: Number(el("iterations").value || 10),
    max_steps_per_round: Number(el("maxStepsPerRound").value || 50),
    max_tokens: String((Number(el("maxTokens").value) || 8) * 1000),
    effort_level: el("effortLevel") ? el("effortLevel").value : "low",
    dataset_path: el("datasetPath").value,
    scoring_mode: FIXED_SCORING_MODE,
    auto_keep_best: true,
    enable_teacher_review: true,
    enable_multi_anchor: true,
    verbose: true,
    auto_score: el("autoScoreToggle") ? el("autoScoreToggle").checked : true,
    auto_accept: el("autoAcceptToggle") ? el("autoAcceptToggle").checked : true,
    max_case_seconds: Number(el("maxCaseSeconds") && el("maxCaseSeconds").value || 25),
  };
  if (selectedProfile === "manual") {
    config.api_type = el("apiType").value;
    config.api_key = el("apiKey").value;
    config.base_url = el("baseUrl").value;
  }
  return config;
}

function renderManualApiConfig() {
  const panel = el("manualApiFields");
  if (!panel) return;
  if (_manualApiExpanded) panel.classList.remove("hidden");
  else panel.classList.add("hidden");
  el("apiConfigBtn").textContent = _manualApiExpanded ? "收起手动 API 配置" : "手动配置 API";
}

function currentApiType() {
  const profile = el("apiProfile").value || "manual";
  if (profile.startsWith("zshrc:")) return profile.split(":", 2)[1];
  return el("apiType").value || "openai";
}

function renderEffortField(preferredValue) {
  const select = el("effortLevel");
  if (!select) return;
  const options = PROVIDER_EFFORT_OPTIONS[currentApiType()] || PROVIDER_EFFORT_OPTIONS.default;
  const current = preferredValue ?? select.value;
  select.innerHTML = options.map((value) => `<option value="${value}">${value}</option>`).join("");
  select.value = options.includes(current) ? current : DEFAULT_EFFORT_LEVEL;
}

const PROFILE_MODEL_PRESETS = {
  "zshrc:openrouter": {
    models: [
      "deepseek/deepseek-v4-flash",
      "deepseek/deepseek-v4-pro",
      "moonshotai/kimi-k2.6",
      "minimax/minimax-m2.7",
    ],
    default: "deepseek/deepseek-v4-pro",
  },
  "zshrc:deepseek": {
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
    default: "deepseek-v4-pro",
  },
  "zshrc:aliyun": {
    models: [
      "qwen-plus",
      "qwen-turbo",
      "qwen3.7-max",
      "deepseek-v4-pro",
      "kimi-k2.6",
      "glm-5.1",
      "MiniMax-M2.5",
    ],
    default: "qwen-plus",
  },
  "manual:aliyun": {
    models: [
      "qwen-plus",
      "qwen-turbo",
      "qwen3.7-max",
      "deepseek-v4-pro",
      "kimi-k2.6",
      "glm-5.1",
      "MiniMax-M2.5",
    ],
    default: "qwen-plus",
  },
};

function renderModelField() {
  const profile = el("apiProfile").value;
  const input = el("model");
  const select = el("modelSelect");
  if (!input || !select) return;
  const presetKey = profile === "manual" ? `manual:${el("apiType").value}` : profile;
  const preset = PROFILE_MODEL_PRESETS[presetKey];
  if (preset) {
    select.innerHTML = preset.models
      .map((m) => `<option value="${m}">${m}</option>`)
      .join("");
    const current = input.value;
    select.value = preset.models.includes(current) ? current : preset.default;
    input.value = select.value;
    select.classList.remove("hidden");
    input.classList.add("hidden");
  } else {
    select.classList.add("hidden");
    input.classList.remove("hidden");
  }
}

async function loadApiProfiles() {
  const data = await fetch("/api/api_profiles").then((r) => r.json());
  const select = el("apiProfile");
  const profiles = Array.isArray(data.profiles) ? data.profiles : [];
  const options = profiles.map((p) => `<option value="${p.id}">${p.label}</option>`);
  options.push('<option value="manual">手动配置</option>');
  select.innerHTML = options.join("");
  select.value = data.selected || (profiles.length > 0 ? profiles[0].id : "manual");
  _automaticApiProfile = profiles.length > 0 ? profiles[0].id : "";
  if (select.value !== "manual") _automaticApiProfile = select.value;
  _manualApiExpanded = select.value === "manual";
  renderManualApiConfig();
  renderEffortField();
  renderModelField();
}

async function hydrateConfigForm() {
  let cfg;
  try {
    cfg = await fetch("/api/config").then((r) => r.json());
  } catch (e) {
    console.error(e);
    return;
  }
  if (!cfg || typeof cfg !== "object") return;
  const setVal = (id, v) => {
    const node = el(id);
    if (node && v !== undefined && v !== null && String(v) !== "") node.value = String(v);
  };
  const setChk = (id, v) => {
    const node = el(id);
    if (node && typeof v === "boolean") node.checked = v;
  };
  setVal("iterations", cfg.iterations);
  setVal("maxStepsPerRound", cfg.max_steps_per_round);
  if (cfg.max_tokens !== undefined && cfg.max_tokens !== null) {
    const tokens = Number(cfg.max_tokens);
    if (Number.isFinite(tokens) && tokens > 0) {
      setVal("maxTokens", Math.round(tokens / 1000));
    }
  }
  setVal("effortLevel", cfg.effort_level);
  setVal("datasetPath", cfg.dataset_path);
  if (cfg.max_case_seconds !== undefined && cfg.max_case_seconds !== null && el("maxCaseSeconds")) {
    setVal("maxCaseSeconds", cfg.max_case_seconds);
  }
  setVal("model", cfg.model);
  setVal("baseUrl", cfg.base_url);
  setVal("apiType", cfg.api_type);
  setChk("autoScoreToggle", cfg.auto_score);
  setChk("autoAcceptToggle", cfg.auto_accept);
  renderEffortField(cfg.effort_level);
  renderModelField();
}

async function saveConfig() {
  const out = await postJson("/api/config", gatherConfig());
  if (!out.ok) {
    alert(out.error || "保存配置失败");
  }
}

async function setDataset() {
  const out = await postJson("/api/upload_dataset", { dataset_path: el("datasetPath").value });
  if (!out.ok) {
    alert(out.error || "设置数据集失败");
  }
}

async function checkApi() {
  await saveConfig();
  const out = await postJson("/api/check_api", {});
  if (!out.ok) {
    alert(out.error || out.message || "API 检查失败");
  }
}

async function runGenius() {
  const code = el("solverEdit").value || "";
  _userEditedSolver = true;
  _suspendSolverRefreshUntil = Date.now() + 4000;
  await saveConfig();
  const out = await postJson("/api/run_genius", { code });
  if (!out.ok) {
    alert(out.error || "评分失败");
  }
}

function syncAutoScoreToggle() {
  const auto = el("autoScoreToggle").checked;
  el("runGeniusBtn").disabled = auto;
  saveConfig().catch((e) => console.error(e));
}

async function runFool() {
  await saveConfig();
  const out = await postJson("/api/run_fool", {});
  if (!out.ok) {
    alert(out.error || "运行 Fool 失败");
  }
}

async function stopRun() {
  await postJson("/api/stop", {});
}

function resetLocalSolverEditorState() {
  _userEditedSolver = false;
  _lastSolverPath = "";
  _suspendSolverRefreshUntil = 0;
  const editor = el("solverEdit");
  if (editor) editor.value = "";
}

async function purgeGlobalNotes() {
  const ok = confirm(
    "若 Fool 正在运行将先停止它（等待当前 LLM 调用结束，最多 30 秒），然后把当前 out/ 目录打包到 out_backups/out_<时间戳>.zip 并整个清空 out/（包括 scoreboard、runs、memory、best_solver 等所有历史最佳）。下一轮将从官方示例 solver 全新开始。确定继续？",
  );
  if (!ok) return;
  const out = await postJson("/api/purge_global_notes", {});
  if (!out.ok) {
    alert(out.error || "清空失败");
    return;
  }
  if (out.archive) {
    alert(`Fool 已停止，out/ 已归档到:\n${out.archive}\n并已整体清空。`);
  } else {
    alert("Fool 已停止，out/ 为空，无需归档。");
  }
}

async function continueRun() {
  await saveConfig();
  const out = await postJson("/api/continue", {});
  if (!out.ok) {
    alert(out.error || "继续运行失败");
  }
}

async function refreshStatus() {
  const status = await fetch("/api/status").then((r) => r.json());
  setText(el("currentIteration"), status.current_iteration);
  setText(el("totalIterations"), status.total_iterations);
  setText(el("currentScore"), status.current_score ?? "-");
  setText(el("completedCases"), status.completed_cases || "0/0");
  const officialEl = el("officialLarge301Score");
  if (officialEl) {
    const v = status.official_large301_score;
    setText(officialEl, v == null ? "-" : Number(v).toFixed(4));
  }
  const scoringEl = el("scoringStatus");
  if (scoringEl) {
    const s = String(status.scoring_status || "");
    const text = s || "-";
    let tone = "neutral";
    if (s.startsWith("正在评分")) tone = "warn";
    else if (s.startsWith("评分完成")) tone = "ok";
    setSignal("scoringStatus", text, tone);
  }
  const stage = String(status.stage || "idle");
  setSignal("stage", stageLabel(stage), stageTone(status));
  const isStopping = stage.toLowerCase() === "stopping";
  ["runFoolBtn", "continueBtn"].forEach((id) => {
    const b = el(id);
    if (b) b.disabled = isStopping;
  });
  const stopBtn = el("stopBtn");
  if (stopBtn) stopBtn.disabled = isStopping;
  setText(el("bestSolverPath"), status.best_solver_path || "-");
  setSignal("aiConnected", status.ai_connected ? "已连接" : "未连接", status.ai_connected ? "ok" : "bad");
  setText(el("aiEndpoint"), status.ai_endpoint || "-");

  const aiMessage = status.ai_message === "not_checked" ? "未检查" : String(status.ai_message || "-");
  const aiTone = status.ai_connected ? "ok" : BAD_WORD_RE.test(aiMessage) ? "bad" : "warn";
  setSignal("aiMessage", aiMessage, aiTone);

  const err = String(status.last_error || "").trim();
  setSignal("lastError", err || "-", err ? "bad" : "neutral");
  const acceptBtn = el("acceptIterationBtn");
  if (acceptBtn) acceptBtn.disabled = !Boolean(status.awaiting_approval);
  const approvalHint = el("approvalHint");
  if (approvalHint) {
    if (status.awaiting_approval) {
      setSignal("approvalHint", `等待接受第 ${status.approval_iteration} 轮`, "warn");
    } else if (el("autoAcceptToggle").checked) {
      setSignal("approvalHint", "自动循环中", "ok");
    } else {
      setSignal("approvalHint", "每轮需手动接受", "neutral");
    }
  }
  renderErrorBanner(status);
}

let _lastReportKey = "";
async function refreshReport() {
  const data = await fetch("/api/latest_report_json").then((r) => r.json());
  const cases = Array.isArray(data.cases) ? data.cases : [];
  const tbody = el("caseBody");
  if (!tbody) return;
  const key = cases.length === 0
    ? "empty"
    : cases.length + "|" + cases.map((c) => `${c.case_name}:${c.score}:${c.covered}/${c.total_tasks}:${c.runtime_ms}`).join(",");
  if (key === _lastReportKey) return;
  if (hasSelectionInside(tbody)) return;
  _lastReportKey = key;
  if (cases.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无结果</td></tr>';
    return;
  }
  const rows = cases.map((c, idx) => {
    const name = String(c.case_name || "-");
    const score = Number(c.score || 0).toFixed(2);
    const covered = Number(c.covered || 0);
    const total = Number(c.total_tasks || 0);
    const pct = Number(c.coverage_pct || 0).toFixed(1);
    const rate = `${covered}/${total}(${pct}%)`;
    const ms = `${Number(c.runtime_ms || 0)}ms`;
    return `<tr><td class="rank">${idx + 1}</td><td>${name}</td><td class="score">${score}</td><td>${rate}</td><td>${ms}</td></tr>`;
  });
  tbody.innerHTML = rows.join("");
}

async function clearScoreboard() {
  if (!confirm("确定清空排行榜？该操作不可撤销。")) return;
  const out = await postJson("/api/scoreboard/clear", {});
  if (!out.ok) {
    alert(out.error || "清空排行榜失败");
    return;
  }
  await refreshScoreboard();
}

let _lastScoreboardKey = "";
async function refreshScoreboard() {
  const data = await fetch("/api/scoreboard").then((r) => r.json());
  const entries = Array.isArray(data.entries) ? data.entries : [];
  const tbody = el("scoreboardBody");
  if (!tbody) return;
  const key = entries.length === 0
    ? "empty"
    : entries.length + "|" + entries.map((e) => `${e.seq}:${e.score}:${e.bucket_min_avg ?? "-"}`).join(",");
  if (key === _lastScoreboardKey) return;
  if (hasSelectionInside(tbody)) return;
  _lastScoreboardKey = key;
  if (entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无成绩</td></tr>';
    return;
  }
  const bySeqDesc = entries
    .filter((e) => e.seq != null)
    .slice()
    .sort((a, b) => Number(b.seq) - Number(a.seq));
  const latestEntry = bySeqDesc[0];
  const prevEntry = bySeqDesc[1];
  let latestClass = "";
  if (latestEntry) {
    if (!prevEntry) {
      latestClass = "latest";
    } else {
      const cur = Number(latestEntry.score);
      const prev = Number(prevEntry.score);
      if (cur < prev) latestClass = "latest latest-up";
      else if (cur > prev) latestClass = "latest latest-down";
      else latestClass = "latest latest-same";
    }
  }
  const latestSeq = latestEntry ? Number(latestEntry.seq) : 0;
  const rows = entries.map((entry, idx) => {
    const score = Number(entry.score).toFixed(2);
    const official = entry.official_large301 == null ? "-" : Number(entry.official_large301).toFixed(2);
    const bucketMin = entry.bucket_min_avg == null ? "-" : Number(entry.bucket_min_avg).toFixed(2);
    const tsRaw = String(entry.ts || "-");
    const ts = tsRaw.replace(/^\d{4}-/, "");
    const seq = entry.seq == null ? "-" : Number(entry.seq);
    const cls = latestSeq > 0 && seq === latestSeq ? ` class="${latestClass}"` : "";
    return `<tr${cls}><td class="rank">${idx + 1}</td><td class="score">${score}</td><td class="score">${official}</td><td class="score">${bucketMin}</td><td>${ts}</td><td>${seq}</td></tr>`;
  });
  tbody.innerHTML = rows.join("");
}

let _lastSolverPath = "";
let _userEditedSolver = false;
let _suspendSolverRefreshUntil = 0;
async function refreshSolver() {
  const data = await fetch("/api/latest_solver").then((r) => r.json());
  const path = data.path || "";
  const code = data.code || "";
  el("solverPath").textContent = path || "-";
  const editor = el("solverEdit");
  const pathChanged = path !== _lastSolverPath;
  const refreshAllowed =
    Date.now() >= _suspendSolverRefreshUntil &&
    !_userEditedSolver &&
    document.activeElement !== editor;
  if (pathChanged) {
    if (refreshAllowed) {
      editor.value = code;
    }
    _lastSolverPath = path;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// True if the user has an active text selection anchored inside `node`. Used
// to defer innerHTML rewrites that would otherwise clobber the selection
// mid-copy. We re-check next poll, so the update is deferred, not skipped.
function hasSelectionInside(node) {
  const sel = window.getSelection && window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return false;
  for (let i = 0; i < sel.rangeCount; i++) {
    const range = sel.getRangeAt(i);
    if (node.contains(range.commonAncestorContainer)) return true;
  }
  return false;
}

let _lastThoughtsKey = "";
async function refreshThoughts() {
  const data = await fetch("/api/thoughts").then((r) => r.json());
  const thoughts = Array.isArray(data.thoughts) ? data.thoughts : [];
  const box = el("thoughtsBox");
  if (!box) return;
  const key = thoughts.length === 0
    ? "empty"
    : thoughts.length + "|" + thoughts.map((t) => {
        const steps = Array.isArray(t.steps) ? t.steps.length : 0;
        return `${t.iteration}:${t.outcome || "-"}:${t.score ?? "-"}:${steps}`;
      }).join(",");
  if (key === _lastThoughtsKey) return;
  if (hasSelectionInside(box)) return;
  _lastThoughtsKey = key;
  if (thoughts.length === 0) {
    box.innerHTML = '<div class="empty">暂无思考记录</div>';
    return;
  }
  box.innerHTML = thoughts.map((item) => {
    const iter = Number(item.iteration || 0);
    const edits = (Array.isArray(item.edit_plan) ? item.edit_plan : []).map((x) => escapeHtml(x)).join("；");
    const buckets = (Array.isArray(item.target_buckets) ? item.target_buckets : []).map((x) => escapeHtml(x)).join("、") || "全部";
    const steps = Array.isArray(item.steps) ? item.steps : [];
    const stepsHtml = (() => {
      if (steps.length === 0) return '<span class="thought-empty">尚未产生步骤</span>';
      const rows = [];
      let pair = 0;
      let pendingIntent = "";
      const renderIntent = () => {
        if (!pendingIntent) return "";
        const txt = escapeHtml(pendingIntent.trim().slice(0, 600));
        pendingIntent = "";
        return txt ? `<div class="thought-intent">${txt}</div>` : "";
      };
      for (const s of steps) {
        const name = String(s.tool_name || "");
        if (name === "(intent)") {
          pendingIntent = (pendingIntent ? pendingIntent + "\n" : "") + String(s.tool_content || "");
          continue;
        }
        pair += 1;
        const tagA = `${pair}.A`;
        const tagQ = `${pair}.Q`;
        const ok = s.tool_ok ? '<span class="signal signal-ok">ok</span>' : '<span class="signal signal-bad">fail</span>';
        const snippet = escapeHtml((s.tool_content || "").trim().slice(0, 160));
        const nameSafe = escapeHtml(name || "?");
        const intentBlock = renderIntent();
        rows.push(`<li><span class="qa-tag qa-a">${tagA}</span> <code>${nameSafe}</code>${intentBlock}</li>`);
        rows.push(`<li><span class="qa-tag qa-q">${tagQ}</span> ${ok}${snippet ? `<div class="thought-snippet">${snippet}</div>` : ""}</li>`);
      }
      if (pendingIntent) {
        pair += 1;
        const intentBlock = renderIntent();
        rows.push(`<li><span class="qa-tag qa-a">${pair}.A</span> <em>intent</em>${intentBlock}</li>`);
      }
      return `<ul class="thought-steps">${rows.join("")}</ul>`;
    })();
    let result = '<span class="signal signal-warn">等待评分</span>';
    if (item.outcome) {
      const outcomeMap = { baseline: "基准", improved: "改进", rollback: "触发护栏", regressed: "退步", neutral: "持平", catastrophic: "崩盘", harness_failed: "harness 失败", duplicate_skipped: "重复跳过未评分" };
      let delta = "-";
      if (item.score_delta != null) {
        const d = Number(item.score_delta);
        if (d < 0) delta = `↓${Math.abs(d).toFixed(2)}（改善）`;
        else if (d > 0) delta = `↑${d.toFixed(2)}（退步）`;
        else delta = "0.00（持平）";
      }
      result = `${outcomeMap[item.outcome] || escapeHtml(item.outcome)}；得分=${Number(item.score).toFixed(2)}；相对变化=${delta}`;
    }
    const bucketDeltas = Array.isArray(item.bucket_deltas) ? item.bucket_deltas : [];
    let bucketRow = "";
    if (bucketDeltas.length > 0) {
      const parts = bucketDeltas.map((b) => {
        const name = escapeHtml(String(b.bucket || "?"));
        const short = name.split("_")[0];
        const d = (b.delta == null) ? null : Number(b.delta);
        let badge;
        if (d == null) {
          badge = `${short}=·`;
        } else if (Math.abs(d) < 0.005) {
          badge = `${short}=·`;
        } else {
          const sign = d < 0 ? "↓" : "↑";
          const cls = d < 0 ? "bucket-better" : "bucket-worse";
          badge = `<span class="bucket-chip ${cls}" title="${name}: ${Number(b.score).toFixed(2)} (best=${b.base_score == null ? "-" : Number(b.base_score).toFixed(2)})">${short}${sign}${Math.abs(d).toFixed(1)}</span>`;
        }
        if (b.is_target) {
          return `<strong class="bucket-target">${badge}</strong>`;
        }
        return badge;
      });
      bucketRow = `<p class="thought-buckets"><span class="thought-label">各桶 Δ：</span>${parts.join(" ")}</p>`;
    }
    const planFilled = item.analysis || item.hypothesis || edits;
    const planBlock = planFilled
      ? `<p><span class="thought-label">重点分析：</span>${escapeHtml(item.analysis) || "-"}</p>
         <p><span class="thought-label">本轮假设：</span>${escapeHtml(item.hypothesis) || "-"}</p>
         <p><span class="thought-label">目标数据集：</span>${buckets}</p>
         <p><span class="thought-label">执行动作：</span>${edits || "-"}</p>`
      : '<p class="thought-empty">等待 final 计划…</p>';
    return `<article class="thought-card">
      <h3>第${iter}轮</h3>
      ${planBlock}
      <p><span class="thought-label">工具调用步骤：</span></p>
      ${stepsHtml}
      <p class="thought-result"><span class="thought-label">结果：</span>${result}</p>
      ${bucketRow}
    </article>`;
  }).join("");
}

async function acceptIteration() {
  const out = await postJson("/api/accept_iteration", {});
  if (!out.ok) alert(out.error || "接受本轮计划失败");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function summarizeAssistantTurn(raw) {
  // Try to pull the first <tool name="..."> or <final> tag and label it.
  const m = raw.match(/<tool[^>]*\bname=["']([^"']+)["']/);
  if (m) return { label: `调用工具 ${m[1]}`, kind: "tool" };
  const m2 = raw.match(/<tool>\s*\{\s*"name"\s*:\s*"([^"]+)"/);
  if (m2) return { label: `调用工具 ${m2[1]}`, kind: "tool" };
  if (/<final>/.test(raw)) return { label: "提交最终方案 <final>", kind: "final" };
  return { label: "回复", kind: "text" };
}

function summarizeUserTurn(content, turnRole) {
  if (turnRole === "system") return { label: "系统提示", kind: "system" };
  const m = content.match(/^\[tool_result\s+name=(\S+)\s+status=(\w+)\]/);
  if (m) return { label: `工具结果 ${m[1]} (${m[2]})`, kind: "tool_result" };
  if (/^Round:\s*\d+/.test(content)) {
    const rm = content.match(/^Round:\s*(\d+)/);
    return { label: `第 ${rm ? rm[1] : "?"} 轮开始`, kind: "round_header" };
  }
  return { label: "提示", kind: "text" };
}

function renderChatBubble(e) {
  const meta = e.meta || {};
  const isAssistant = e.direction === "in";
  const role = isAssistant ? "assistant" : (meta.turn_role || "user");
  const speaker = isAssistant ? "🤖 Assistant" : role === "system" ? "⚙️ System" : "👤 User";

  const content = e.content || "";
  const summary = isAssistant
    ? summarizeAssistantTurn(content)
    : summarizeUserTurn(content, role);

  const metaBits = [];
  if (meta.iteration !== undefined) metaBits.push(`第${meta.iteration}轮`);
  if (meta.step !== undefined) metaBits.push(`step ${meta.step}`);
  if (isAssistant) {
    if (meta.prompt_tokens) {
      const hit = meta.cached_tokens || 0;
      const pct = meta.prompt_tokens ? Math.round((hit * 100) / meta.prompt_tokens) : 0;
      metaBits.push(`in=${meta.prompt_tokens}(cache ${pct}%)`);
    }
    if (meta.completion_tokens) metaBits.push(`out=${meta.completion_tokens}`);
    if (meta.finish_reason) metaBits.push(`finish=${meta.finish_reason}`);
  } else {
    if (meta.max_tokens) metaBits.push(`max_tokens=${meta.max_tokens}`);
  }
  const metaStr = metaBits.length ? metaBits.join(" · ") : "";

  const side = isAssistant ? "assistant" : "user";
  const collapsedByDefault = content.length > 280;
  const preview = collapsedByDefault ? content.slice(0, 280) + "…" : content;

  const detailsBlock = collapsedByDefault
    ? `<details class="chat-details"><summary>展开完整内容（${content.length} 字符）</summary><pre class="chat-full">${escapeHtml(content)}</pre></details>`
    : "";

  return `<div class="chat-row chat-${side}">
    <div class="chat-bubble chat-bubble-${side} chat-kind-${summary.kind}">
      <div class="chat-head">
        <span class="chat-speaker">${escapeHtml(speaker)}</span>
        <span class="chat-tag">${escapeHtml(summary.label)}</span>
        <span class="chat-ts">${escapeHtml(e.ts || "")}</span>
      </div>
      <pre class="chat-body">${escapeHtml(preview)}</pre>
      ${detailsBlock}
      ${metaStr ? `<div class="chat-meta">${escapeHtml(metaStr)}</div>` : ""}
    </div>
  </div>`;
}

let _lastLlmDialogueKey = "";

async function refreshLlmDialogue() {
  const data = await fetch("/api/llm_dialogue").then((r) => r.json());
  const box = el("llmDialogueBox");
  if (!box) return;
  const entries = Array.isArray(data.dialogue) ? data.dialogue : [];
  const key = entries.length
    ? entries.length + "|" + (entries[entries.length - 1].ts || "") + "|" + (entries[entries.length - 1].direction || "")
    : "empty";
  if (key === _lastLlmDialogueKey) return;
  if (hasSelectionInside(box)) return;
  _lastLlmDialogueKey = key;

  if (entries.length === 0) {
    box.innerHTML = '<div class="empty">暂无对话</div>';
    return;
  }
  const html = entries.map(renderChatBubble).join("");
  const wasAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.innerHTML = html;
  if (wasAtBottom) box.scrollTop = box.scrollHeight;
}

async function poll() {
  try {
    await refreshStatus();
    await refreshReport();
    await refreshSolver();
    await refreshScoreboard();
    await refreshThoughts();
    await refreshLlmDialogue();
  } catch (err) {
    console.error(err);
  }
}

function bind() {
  el("saveConfigBtn").addEventListener("click", saveConfig);
  el("checkApiBtn").addEventListener("click", checkApi);
  el("apiProfile").addEventListener("change", () => {
    if (el("apiProfile").value !== "manual") _automaticApiProfile = el("apiProfile").value;
    _manualApiExpanded = el("apiProfile").value === "manual";
    renderManualApiConfig();
    renderEffortField();
    renderModelField();
  });
  el("apiConfigBtn").addEventListener("click", () => {
    if (_manualApiExpanded) {
      _manualApiExpanded = false;
      if (_automaticApiProfile) el("apiProfile").value = _automaticApiProfile;
    } else {
      el("apiProfile").value = "manual";
      _manualApiExpanded = true;
    }
    renderManualApiConfig();
    renderModelField();
  });
  el("modelSelect").addEventListener("change", () => {
    el("model").value = el("modelSelect").value;
  });
  el("apiType").addEventListener("change", () => {
    renderEffortField();
    renderModelField();
  });
  el("setDatasetBtn").addEventListener("click", setDataset);
  el("runGeniusBtn").addEventListener("click", runGenius);
  const loadBtn = el("loadSolverBtn");
  const fileInput = el("solverFileInput");
  if (loadBtn && fileInput) {
    loadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      if (!/\.py$/i.test(f.name)) {
        alert("请选择 .py 文件");
        fileInput.value = "";
        return;
      }
      const text = await f.text();
      el("solverEdit").value = text;
      _userEditedSolver = true;
      el("solverPath").textContent = `(loaded) ${f.name}`;
      fileInput.value = "";
    });
  }
  const clearBtn = el("clearScoreboardBtn");
  if (clearBtn) clearBtn.addEventListener("click", clearScoreboard);
  el("autoScoreToggle").addEventListener("change", syncAutoScoreToggle);
  syncAutoScoreToggle();
  el("autoAcceptToggle").addEventListener("change", () => {
    saveConfig().catch((e) => console.error(e));
  });
  el("acceptIterationBtn").addEventListener("click", acceptIteration);
  const editor = el("solverEdit");
  editor.addEventListener("input", () => {
    _userEditedSolver = true;
  });
  editor.addEventListener("paste", () => {
    _userEditedSolver = true;
  });
  el("runFoolBtn").addEventListener("click", runFool);
  el("stopBtn").addEventListener("click", stopRun);
  el("continueBtn").addEventListener("click", continueRun);
  const purgeNotesBtn = document.getElementById("purgeNotesBtn");
  if (purgeNotesBtn) purgeNotesBtn.addEventListener("click", purgeGlobalNotes);
}

async function initialize() {
  await loadApiProfiles();
  await hydrateConfigForm();
  bind();
  resetLocalSolverEditorState();
  await poll();
  setInterval(poll, 1500);
}

initialize();
