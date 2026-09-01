# Aliyun Bailian LLM Service 接入设计

**日期**：2026-06-03
**作者**：sunnyseed + Copilot
**目标**：在现有 MTASA 配置与运行链路中新增独立的 `api_type=aliyun`，让用户可以直接选择“阿里云百炼”作为 LLM 服务，同时继续复用现有 OpenAI-compatible Chat Completions 请求实现。

## 核心结论

阿里云百炼在本项目中按“**独立 provider 名称 + OpenAI 兼容协议实现**”接入：

1. **配置层显式出现 `aliyun`**，避免用户把它误当成 `openai` 手动伪装。
2. **传输层复用现有 OpenAI-compatible 分支**，不新建第二套 HTTP 客户端。
3. **默认固定北京地域 base URL**：`https://dashscope.aliyuncs.com/compatible-mode/v1`
4. **自动发现只识别 `DASHSCOPE_API_KEY`**。

这保证了用户心智清晰、改动面小、对现有 provider 风险最低。

## 范围

本次设计只覆盖“阿里云百炼作为新 LLM service 的接入参数与配置流”：

- 前端手动配置增加 `aliyun`
- `.zshrc` 自动发现增加 `DASHSCOPE_API_KEY`
- 后端 runtime config 支持 `api_type=aliyun`
- LLM 调用层支持 `aliyun` 默认 endpoint 解析
- 配置示例与说明文档补齐

**不包含**：

- 百炼原生 DashScope API 的单独接入
- 地域选择器 / Workspace 专用配置项
- 百炼专属思考参数、工具参数、Responses API
- 多地域 key 自动切换

## 现状

当前仓库已有 5 类 provider 入口：

- `frontend/server.py` 中的 `API_PROFILE_VARS` 管理 `.zshrc` 自动发现
- `frontend/app.js` 的手动配置 UI 负责提交 `api_type/api_key/base_url/model`
- `config.example.json` 提供静态示例
- `fool/llm_client.py` 统一处理 `openai/openrouter/deepseek/custom/claude`
- `probe_llm_connection()` 和 `call_llm_meta()` 共同负责连通性检查与正式调用

其中 `openai/openrouter/deepseek/custom` 已经共享一条 OpenAI-shape Chat Completions 请求链路，因此百炼最自然的落点是复用这条分支，而不是新增完全独立的 HTTP 实现。

## 方案对比

### 方案 A：新增独立 `api_type=aliyun`，底层复用 OpenAI-compatible 分支（推荐）

优点：

- UI、配置、日志语义清晰
- 用户不需要知道“百炼其实兼容 OpenAI”
- 改动集中，测试面可控
- 后续若要补百炼专属行为，也有独立 provider 名称可扩展

缺点：

- 需要在前端、server、llm_client、文档多点补齐 `aliyun`

### 方案 B：继续使用 `api_type=openai`，只让用户手填百炼 base URL

优点：

- 代码改动最少

缺点：

- 用户体验差，配置语义不准确
- 日志与排错时无法区分 OpenAI 和百炼
- 自动发现 `.zshrc` 的入口不自然

### 方案 C：同时保留独立 `aliyun` 和 `openai` 伪装入口

优点：

- 兼容性最宽

缺点：

- 入口重复
- 文档和测试矩阵膨胀
- 容易出现两条路径行为不一致

最终选择 **方案 A**。

## 设计

## 1. Provider 标识与默认行为

新增 provider 标识：

- `api_type = "aliyun"`

默认行为：

- 默认 base URL 视为 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- 最终 chat completions endpoint 为：
  `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 手动配置时允许用户覆写 `base_url`，但默认值保持北京地域
- 自动发现时只识别 `DASHSCOPE_API_KEY`

设计意图：

- 对普通用户，百炼应表现为“拿 key 和 model 就能用”
- 对高级用户，仍保留通过 `base_url` 覆写的出口

## 2. 前端配置面

### `frontend/app.js`

需要新增三类行为：

1. **手动配置下拉新增 `aliyun`**
2. **模型预设新增百炼常用模型**
3. **保存配置时把 `api_type=aliyun` 原样提交给后端**

模型预设保持轻量，只内置少量稳定候选，例如：

- `qwen-plus`
- `qwen-turbo`
- `qwen-max`

原因：

- 这些名字短、通用、便于第一次接入
- 不在前端硬编码过长的全量模型清单
- 后续若用户要用别的 Qwen 型号，仍可手动输入

不单独新增“地域”或“WorkspaceId”字段，因为本轮需求已经确定为默认北京固定地址。

## 3. 自动发现与运行时配置

### `frontend/server.py`

需要在 `API_PROFILE_VARS` 新增：

- `aliyun.keys = ("DASHSCOPE_API_KEY",)`
- `aliyun.base_urls = ("DASHSCOPE_BASE_URL", "ALIYUN_BAILIAN_BASE_URL")`

虽然自动发现只要求 key 使用 `DASHSCOPE_API_KEY`，但 base URL 环境变量可宽松兼容，以便未来有自定义地域时不必改 schema。

`_discover_zshrc_profiles()` 和 `_effective_api_config()` 不需要新分支，只要数据表里有 `aliyun` 即可自动工作。

默认 profile 选择策略不强制把 `aliyun` 提到最前；沿用当前逻辑即可，避免影响已有 deepseek/openrouter 默认选择习惯。

## 4. LLM 调用层

### `fool/llm_client.py`

百炼按 OpenAI-compatible 处理，具体包括：

- `_resolve_chat_endpoint()` 的默认映射增加：
  - `aliyun -> https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- `call_llm_meta()` 把 `aliyun` 纳入 OpenAI-shape 分支
- `probe_llm_connection()` 也把 `aliyun` 纳入相同分支

请求头继续使用：

- `Authorization: Bearer <api_key>`
- `Content-Type: application/json`

请求体继续沿用当前字段：

- `model`
- `messages`
- `temperature`
- `max_tokens`
- `response_format`（json_mode 时）

这样实现的前提依据是阿里云百炼明确提供 OpenAI 兼容 Chat Completions 接口，并使用 OpenAI SDK 兼容的 `base_url + api_key + model` 组合。

## 5. 配置示例与文档

### `config.example.json`

`config.example.json` **不改默认 provider**，继续保持当前通用默认值，避免影响已有 OpenAI 路径的首次使用体验。

百炼示例改为放在 `README.md` 中明确给出，使用最小可运行配置：

- `"api_type": "aliyun"`
- `"api_key": "$DASHSCOPE_API_KEY"`
- `"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"`
- `"model": "qwen-plus"`

这样处理的原因是：

- `config.example.json` 继续充当“仓库默认模板”
- `README` 单独承担“provider-specific copy-paste 示例”
- 不引入第二份示例配置文件，也不让默认模板偏向某个云厂商

## 错误处理

延续现有错误处理，不加百炼专属分支：

- 缺少 key：仍返回 `API key is empty`
- HTTP 非 2xx：继续走 `LLMHTTPError`
- 返回体 JSON 结构异常：继续走现有解析失败路径
- 用户手动覆写了错误的 `base_url`：错误直接暴露，不做静默兜底

原因：

- 百炼本轮只是 provider 接入，不应引入 provider-specific 隐式 fallback
- 保持错误透明，便于用户自己修正配置

## 测试

需要补齐已有测试体系中的最小覆盖：

1. `fool/llm_client.py`
   - `aliyun` 默认 endpoint 解析正确
   - `aliyun` 进入 OpenAI-compatible 请求分支

2. `frontend/server.py`
   - `.zshrc` 中 `DASHSCOPE_API_KEY` 可被识别为 `zshrc:aliyun`
   - `_effective_api_config()` 能把 profile 还原为 `api_type=aliyun`

3. `frontend/app.js` 对应后端契约
   - 通过现有前端配置接口验证 `api_type=aliyun` 能透传

4. 文档/示例
   - README 中新增百炼示例，且与实际默认行为一致

验收标准：

- 手动配置可直接选 `aliyun`
- `.zshrc` 中仅设置 `DASHSCOPE_API_KEY` 时可自动发现百炼 profile
- 不填 `base_url` 时默认打到北京兼容接口
- 现有 openai/openrouter/deepseek/claude 行为不变

## 影响文件

预计涉及：

- `frontend/server.py`
- `frontend/app.js`
- `fool/llm_client.py`
- `README.md`
- `genius/tests/test_llm_client.py`
- 可能补充 `frontend/server.py` 对应测试文件（若已有覆盖入口则复用）

## 风险与控制

### 风险 1：把百炼做成独立 provider 后，和 OpenAI 逻辑逐渐漂移

控制：

- 本轮只允许 `aliyun` 复用 OpenAI-compatible 分支
- 不复制粘贴第二套请求实现

### 风险 2：前端硬编码模型预设过多，后续快速过期

控制：

- 只提供少量推荐模型
- 始终保留自由输入

### 风险 3：默认 base URL 过强，阻碍未来国际地域

控制：

- 默认固定北京，但仍保留 `base_url` 手动覆写入口

## 实施边界

本 spec 的目标是“**让百炼成为现有 MTASA 中一个一等公民的 provider 选项**”，不是把百炼全部能力接进来。只要用户能够：

1. 选中 `aliyun`
2. 填入或自动发现 `DASHSCOPE_API_KEY`
3. 选择 `qwen-*` 模型
4. 直接完成 API probe 和 Fool 运行

这次改动就算完成。
