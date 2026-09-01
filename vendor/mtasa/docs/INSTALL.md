# MTASA 安装手册

## 系统要求

- **Python 3.10+**（运行 frontend / fool / genius 主代码，使用 `dict[str, Any]`、`X | None` 等 3.10 语法）
- **Python 3.6**（必需，Genius 强制 solver 在 `python3.6` 子进程下运行；线上沙箱也是 3.6）
- **Git**

两个 Python 版本都要装，互不替代。

---

## 一次性环境准备

### 1. 安装 Python 3.6（solver 运行时）

macOS 已没有官方 3.6 Homebrew formula，用 **pyenv** 装：

```bash
brew install pyenv
pyenv install 3.6.15
pyenv global system 3.6.15   # system 优先；3.6.15 暴露 python3.6 shim
which python3.6              # 确认 ~/.pyenv/shims/python3.6
```

确保 `~/.zshrc`（或 `~/.bashrc`）里有：

```bash
export PYENV_ROOT="$HOME/.pyenv"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"
```

**Linux（apt，旧版 ubuntu 自带）**：

```bash
sudo apt install python3.6
```

新版 ubuntu 仓库已撤下 3.6，同样建议用 pyenv。

> 如果 `which python3.6` 找不到，Genius 会直接 `FATAL: python_missing` 拒绝运行。

### 2. 克隆仓库

```bash
git clone https://github.com/Nerolithos/MTASA.git
cd MTASA
```

### 3. 主环境（Python 3.10+）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install tiktoken pytest
```

仓库没有 `requirements.txt`——主代码只依赖标准库 + `tiktoken`（LLM token 计数）+ `pytest`（测试）。

---

## 验证安装

```bash
# 主环境就绪
python3 -c "import tiktoken; print('ok')"

# Python 3.6 就绪
python3.6 -c "print('py3.6 ok')"

# 跑测试套件（应该 303 passed）
python3 -m pytest genius/tests -q

# 跑一次 Genius 评分（验证 solver 子进程链路）
python3 genius/genius_judge.py \
  --solver fool/templates/solver_minimal.py \
  --input-dir data/sample_10_cases \
  --report /tmp/install_test_report.txt
```

最后一条若成功打印 `average_score=...`，说明 Python 3.6 子进程链路、smoke gate、评分流程都通了。

---

## 启动前端

```bash
source .venv/bin/activate
python3 run_local.py
```

自动从 7860 起找空闲端口，浏览器打开输出的 URL。LLM API key 可以：

- 从 `~/.zshrc` 自动读取（支持 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`）
- 或在前端手动填入

---

## 常见问题

**`python_missing` FATAL** — Genius 找不到 `python3.6`。确认 `which python3.6` 有输出，不在 PATH 就用绝对路径：

```bash
python3 genius/genius_judge.py --python-cmd ~/.pyenv/versions/3.6.15/bin/python3.6 ...
```

**`smoke FAIL` 在你刚写的新 solver 上** — 不是 bug，是设计：solver 末尾必须有 `_finalize` 兜底层。参考 `fool/templates/solver_minimal.py` 末尾的实现。

**测试用例需要绕过 smoke** — 设环境变量：

```bash
GENIUS_SKIP_SMOKE=1 python3 -m pytest ...
```

或在 `run_judge()` 直接传 `skip_smoke=True`。生产代码（Fool / 前端）严禁设置。

**`out/runtime_config.json` 总是 dirty** — 每次运行重写，`api_key` 已自动抹空。可忽略，或加入 `.git/info/exclude`。
