# ⚡ MUTeki-Evolve

> **Autonomous, Multi-Round Strategy Evolution for AI Cybersecurity Swarms**

MUTeki-Evolve combines an **MTASA-inspired Strategy Evolution Loop** (the Brain) with **Muteki's Multi-Agent Swarm Core** (the Muscle). Instead of executing one-shot static prompts, MUTeki-Evolve generates structured strategy directives, runs bounded target investigations, scores evidence progress, and autonomously evolves smarter strategies round-after-round.

---

## 📸 Interface & Command Center

![MUTeki-Evolve Desktop Interface](screenshots/muteki-evolve-desktop-final.jpg)

### Mobile & Responsive View
![MUTeki-Evolve Mobile View](screenshots/muteki-evolve-mobile-final.jpg)

---

## 🐣 How It Works (Simplified for Anyone)

Imagine a security investigation like playing a game of chess:

```text
 ┌─────────────────────────────────────────────────────────────────┐
 │               MUTeki-Evolve (The Strategic Brain)              │
 │                                                                 │
 │  1. Creates Strategy  ──►  2. Translates for Swarm              │
 │  5. Evolves Next Rev  ◄──  4. Scores Evidence & Target Status   │
 └────────────────────────────────┬────────────────────────────────┘
                                  │ Directs Execution
                                  ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                Muteki Engine (The Swarm Muscle)                 │
 │                                                                 │
 │  • Spawns Workers (Grok, Codex, Claude)                         │
 │  • Executes Shell Commands, Tools, and HTTP Requests            │
 │  • Emits Real-Time SSE Investigation Stream                      │
 └─────────────────────────────────────────────────────────────────┘
```

1. **Strategy Creation (Round 1)**: MUTeki-Evolve starts with an initial strategy (e.g. *Reconnaissance-first*).
2. **Swarm Execution**: The adapter package sends this strategy to Muteki, which spawns autonomous workers (like **xAI Grok**) to investigate the target.
3. **Evidence Normalization**: As workers execute bash commands and tools, raw outputs are parsed into clean, structured `Evidence` cards.
4. **Progress Scoring**: The scoring engine evaluates the progress (0–100%) and checks if the challenge was solved.
5. **Strategy Evolution**: Our Teacher/Reviewer analyzes what worked and what failed, producing an improved strategy (e.g. *Authentication & Access Control focus*) for Round 2.

---

## ✨ Key Features

- 🧠 **MTASA Strategy Evolution**: Multi-round closed loop (Strategy $\rightarrow$ Swarm Run $\rightarrow$ Evidence Score $\rightarrow$ Evolved Strategy).
- 🤖 **xAI Grok Integration**: Fully integrated with xAI Grok using `XAI_API_KEY` (no paid CLI subscriptions required).
- 🛡️ **Trusted Sandbox Registry**: Enforces target isolation—only explicitly registered targets in `ctf_loader.py` can be investigated.
- 📊 **Real-Time Command Center**: Dark-mode React dashboard with Framer Motion animations, strategy lineage tree, live evidence logs, and progress score gauges.
- 🐍 **Python 3.10–3.13 Compatible**: Clean, fully typed contracts with 293/293 passing unit & integration tests.

---

## 🚀 Quickstart Guide

### Prerequisites
- Python $\ge$ 3.10 (Python 3.13 recommended)
- Node.js $\ge$ 20 LTS
- `uv` Python package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

---

### Step 1: Clone & Setup

```bash
# Clone repository
git clone https://github.com/shamitha311/MUTeki-Evolve-Foundation.git
cd MUTeki-Evolve-Foundation

# Create Python 3.13 virtual environment
uv venv --python 3.13
source .venv/bin/activate

# Install dependencies
uv pip install -e .
```

---

### Step 2: Configure Environment

Copy `.env.example` to `.env` and set your xAI API Key:

```bash
cp .env.example .env
```

Edit `.env`:
```env
MUTEKI_MODE=real
MUTEKI_WORKER_ENGINE=grok
XAI_API_KEY=xai-your-api-key-here
```

---

### Step 3: Run the Custom React UI Dashboard

```bash
# Navigate to UI directory
cd artifacts/muteki-evolve

# Install frontend dependencies
npm install

# Launch Vite dev server
npx vite --host 0.0.0.0 --port 5173
```

Open your browser at **`http://localhost:5173`** to access the MUTeki-Evolve Command Center!

---

### Step 4: Run the Autonomous Strategy Evolution Backend

In a new terminal tab:

```bash
cd ~/MUTeki-Evolve-Foundation
source .venv/bin/activate
export PYTHONPATH=".:vendor/muteki"
export MUTEKI_MODE="real"

# Run A/B integration acceptance test
python integration/run_real.py

# Run full multi-round autonomous strategy evolution
python -m orchestration.orchestrator
```

---

## 🏛️ Project Architecture & File Layout

```text
MUTeki-Evolve-Foundation/
├── app/                      # Project-owned domain models (Strategy, Evidence, Target)
├── muteki_adapter/           # Adapter bridging MUTeki-Evolve with vendor/muteki
│   ├── adapter.py            # RealMutekiAdapter execution & event stream runner
│   ├── translator.py         # Strategy -> Muteki Challenge JSON payload translator
│   ├── event_normalizer.py   # Raw SSE stream -> InvestigationResult normalizer
│   └── config.py             # Environment configuration (grok, codex, claude)
├── orchestration/            # Closed-loop MTASA evolution engine
│   ├── orchestrator.py       # Multi-round strategy evolution orchestrator
│   ├── registry.py           # TrustedTargetRegistry boundary enforcement
│   └── ctf_loader.py         # Pre-approved sandbox targets (vulnweb-testphp)
├── artifacts/muteki-evolve/  # High-performance React + Vite + Tailwind dashboard
├── bin/                      # Engine CLI bridges (bin/grok, bin/grok.cmd)
├── tools/                    # Zero-dependency xAI Grok API bridge (tools/grok_cli.py)
├── vendor/muteki/            # Vendored Muteki multi-agent swarm kernel
├── tests/                    # Automated test suite (293 tests passing)
└── integration/              # 5-gate A/B strategy acceptance test runner
```

---

## 🧪 Verification & Test Suite

Run the full automated test suite anytime:

```bash
export PYTHONPATH=".:vendor/muteki"
python -m pytest tests/ --tb=short
```

Expected output: `293 passed in 0.90s`

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for details.
