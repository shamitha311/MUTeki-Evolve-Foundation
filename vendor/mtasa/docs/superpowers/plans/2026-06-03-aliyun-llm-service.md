# Aliyun Bailian LLM Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `api_type=aliyun` as a first-class LLM service option, with Beijing DashScope OpenAI-compatible defaults and `DASHSCOPE_API_KEY` auto-discovery.

**Architecture:** Reuse the existing OpenAI-compatible request path in `fool/llm_client.py`, but expose Aliyun explicitly in config/UI/profile discovery so users can select it directly. Keep the feature small: one new provider name, one default endpoint, one env var family, and one README example.

**Tech Stack:** Python 3.9 stdlib HTTP client, stdlib `http.server` frontend backend, vanilla JS frontend, pytest

---

## File map

- `fool/llm_client.py` — provider routing and default endpoint resolution
- `frontend/server.py` — profile discovery, effective config, runtime defaults
- `frontend/app.js` — manual provider dropdown and model presets
- `genius/tests/test_llm_client.py` — provider endpoint/probe tests
- `genius/tests/test_file_submission_contract.py` — `.zshrc` profile discovery tests
- `README.md` — Aliyun setup example

### Task 1: Add Aliyun support in the provider layer

**Files:**
- Modify: `genius/tests/test_llm_client.py`
- Modify: `fool/llm_client.py`

- [ ] **Step 1: Write the failing llm_client tests**

Add these tests to `genius/tests/test_llm_client.py`:

```python
def test_aliyun_uses_dashscope_compatible_endpoint() -> None:
    assert (
        client._resolve_chat_endpoint("aliyun", None)
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )


def test_aliyun_probe_uses_openai_compatible_probe_budget(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_llm(**kwargs) -> str:
        captured["api_type"] = kwargs["api_type"]
        captured["max_tokens"] = kwargs["max_tokens"]
        return "MTASA_OK"

    monkeypatch.setattr(client, "call_llm", fake_call_llm)

    result = client.probe_llm_connection("aliyun", "token", "qwen-plus")

    assert result["ok"] is True
    assert captured["api_type"] == "aliyun"
    assert captured["max_tokens"] == 32
```

- [ ] **Step 2: Run the llm_client tests and confirm they fail**

Run:

```bash
python -m pytest genius/tests/test_llm_client.py -v
```

Expected: FAIL because `aliyun` is not yet in `_resolve_chat_endpoint()` or the OpenAI-compatible provider set.

- [ ] **Step 3: Implement the minimal provider support**

Update `fool/llm_client.py` in three places:

```python
default_map = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "custom": "https://api.openai.com/v1/chat/completions",
}
```

```python
if api_type in {"openai", "openrouter", "deepseek", "aliyun", "custom"}:
    endpoint = _resolve_chat_endpoint(api_type, base_url)
```

```python
"endpoint": _resolve_chat_endpoint(api_type, base_url)
if api_type in {"openai", "openrouter", "deepseek", "aliyun", "custom"}
```

- [ ] **Step 4: Run the llm_client tests and confirm they pass**

Run:

```bash
python -m pytest genius/tests/test_llm_client.py -v
```

Expected: PASS for existing tests plus the new `aliyun` cases.

- [ ] **Step 5: Commit**

```bash
git add genius/tests/test_llm_client.py fool/llm_client.py
git commit -m "llm: add aliyun provider support"
```

### Task 2: Add Aliyun profile discovery and frontend selection

**Files:**
- Modify: `genius/tests/test_file_submission_contract.py`
- Modify: `frontend/server.py`
- Modify: `frontend/app.js`

- [ ] **Step 1: Write the failing profile discovery test**

Add a second profile-discovery test to `genius/tests/test_file_submission_contract.py`:

```python
def test_zshrc_profile_discovery_supports_aliyun(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "export DASHSCOPE_API_KEY='dash-token'\n",
        encoding="utf-8",
    )

    profiles = _discover_zshrc_profiles(zshrc)

    assert profiles == [
        {
            "id": "zshrc:aliyun",
            "api_type": "aliyun",
            "label": ".zshrc - aliyun",
            "key_name": "DASHSCOPE_API_KEY",
            "base_url": "",
        }
    ]
```

- [ ] **Step 2: Run the server/profile test file and confirm it fails**

Run:

```bash
python -m pytest genius/tests/test_file_submission_contract.py -v
```

Expected: FAIL because `frontend/server.py` does not yet know about `aliyun` / `DASHSCOPE_API_KEY`.

- [ ] **Step 3: Add backend profile discovery support**

Update `frontend/server.py`:

```python
API_PROFILE_VARS = {
    "openai": {...},
    "claude": {...},
    "openrouter": {...},
    "deepseek": {...},
    "aliyun": {
        "keys": ("DASHSCOPE_API_KEY",),
        "base_urls": ("DASHSCOPE_BASE_URL", "ALIYUN_BAILIAN_BASE_URL"),
    },
    "custom": {...},
}
```

Do **not** change `_selected_api_profile()` default priority order; Aliyun should be discoverable but not forcibly preferred over existing deepseek/openrouter shortcuts.

- [ ] **Step 4: Add frontend provider choice and model presets**

Update `frontend/app.js` in two places:

1. Keep manual config flow unchanged:

```javascript
if (selectedProfile === "manual") {
  config.api_type = el("apiType").value;
  config.api_key = el("apiKey").value;
  config.base_url = el("baseUrl").value;
}
```

2. Add Aliyun model presets:

```javascript
const PROFILE_MODEL_PRESETS = {
  "zshrc:openrouter": { ... },
  "zshrc:deepseek": { ... },
  "zshrc:aliyun": {
    models: ["qwen-plus", "qwen-turbo", "qwen-max"],
    default: "qwen-plus",
  },
};
```

Also update the HTML-backed manual provider options source if `apiType` is populated there from static markup or JS initialization; make sure `aliyun` appears in the same place as `openai`, `claude`, `openrouter`, `deepseek`, and `custom`.

- [ ] **Step 5: Re-run the profile discovery tests**

Run:

```bash
python -m pytest genius/tests/test_file_submission_contract.py -v
```

Expected: PASS, including the new `zshrc:aliyun` case.

- [ ] **Step 6: Do one manual frontend sanity check**

Run:

```bash
python run_local.py
```

Verify in the browser:

- manual API type dropdown includes `aliyun`
- `.zshrc` profile list shows `aliyun` when `DASHSCOPE_API_KEY` exists
- selecting Aliyun keeps `model` editable or preset-selectable

- [ ] **Step 7: Commit**

```bash
git add genius/tests/test_file_submission_contract.py frontend/server.py frontend/app.js
git commit -m "frontend: add aliyun api profile"
```

### Task 3: Add user-facing Aliyun setup docs

**Files:**
- Modify: `README.md`
- Verify: `docs/superpowers/specs/2026-06-03-aliyun-llm-service-design.md`

- [ ] **Step 1: Add a README setup example**

Add a short Aliyun section near the existing API/config instructions:

```md
### 阿里云百炼（Aliyun Bailian / DashScope）

MTASA 支持把百炼作为独立的 `api_type=aliyun` 接入，底层走 OpenAI 兼容接口。

示例配置：

```json
{
  "api_type": "aliyun",
  "api_key": "$DASHSCOPE_API_KEY",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "model": "qwen-plus"
}
```

如果你在 `~/.zshrc` 中配置了 `DASHSCOPE_API_KEY`，前端会自动发现该 profile。
```

- [ ] **Step 2: Review the README text for consistency**

Check that the docs match the code decisions:

- provider name is `aliyun`
- env var is `DASHSCOPE_API_KEY`
- default region is Beijing
- protocol is OpenAI-compatible chat completions

- [ ] **Step 3: Run the focused backend tests again**

Run:

```bash
python -m pytest genius/tests/test_llm_client.py genius/tests/test_file_submission_contract.py -v
```

Expected: PASS for all focused tests after docs/code are aligned.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add aliyun bailian setup example"
```

## Self-review notes

- **Spec coverage:** provider name, Beijing default endpoint, `DASHSCOPE_API_KEY` discovery, UI/provider exposure, and README example are all covered by Tasks 1-3.
- **Placeholder scan:** no TBD/TODO placeholders remain; every code-changing task names exact files and concrete snippets.
- **Type consistency:** the plan uses the same names throughout: `api_type=aliyun`, `DASHSCOPE_API_KEY`, `qwen-plus`, and `https://dashscope.aliyuncs.com/compatible-mode/v1`.
