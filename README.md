# ⚡ MUTeki-Evolve

> **Autonomous, Multi-Round Strategy Evolution for AI Cybersecurity Swarms**  
> *Hackathon Submission & System Demonstration*

---

## 🎯 Executive Summary (For Judges & Reviewers)

Existing AI security agents execute single-shot prompts that stall when hitting complex barriers. **MUTeki-Evolve** solves this by pairing an **MTASA-inspired Strategy Evolution Loop** (the Brain) with **Muteki's Multi-Agent Swarm Core** (the Muscle).

Instead of guessing raw shell commands, MUTeki-Evolve formulates high-level strategy directives, dispatches autonomous workers (like **xAI Grok**), normalizes evidence, scores progress, and autonomously evolves smarter strategies round-after-round until the objective is solved (100/100).

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
 │  • Spawns Workers (Grok via XAI_API_KEY, Codex, Claude)        │
 │  • Executes Shell Commands, Tools, and HTTP Requests            │
 │  • Emits Real-Time SSE Investigation Stream                      │
 └─────────────────────────────────────────────────────────────────┘
```

---

## 📸 System Walkthrough & UI Showcase

### 1. Main Command Center (Trusted Target: `http://testphp.vulnweb.com`)
The dashboard displays the active target (`testphp.vulnweb.com Assessment Target`), current strategy objective, evidence ledger, and real-time score progression.

![MUTeki-Evolve Command Center Desktop](screenshots/muteki-evolve-desktop-final.jpg)

### 2. Multi-Round Strategy Evolution (Revision A → Revision B → Revision C)
- **Round 1 (Revision A)**: Deploys a *Reconnaissance-first* strategy to map the target surface. Progress score: **28/100**.
- **Round 2 (Revision B)**: Analyzes evidence signals and shifts focus to *Authentication & SQL Injection vectors*. Progress score: **72/100**.
- **Round 3 (Revision C)**: Verifies captured hashes and completes the assessment. Progress score: **100/100 (SOLVED)**.

![MUTeki-Evolve Mobile / Responsive View](screenshots/muteki-evolve-mobile-final.jpg)

---

## 🔍 Visual Proof & Technical Architecture

### 1. Isolated Ubuntu VM Execution via SSH
All operations execute inside a disposable, sandboxed Ubuntu VM (`vboxuser@Linux` / `SSH: 192.168.56.101`) to ensure complete containment.

### 2. Grok AI Engine & Zero-Dependency CLI Bridge (`tools/grok_cli.py`)
We built a standalone CLI bridge (`tools/grok_cli.py` and `bin/grok`) that routes Muteki driver prompts directly to xAI's API (`api.x.ai`) using `XAI_API_KEY`—eliminating any requirement for paid CLI web subscriptions.

```bash
# Environment configuration (.env)
MUTEKI_MODE=real
MUTEKI_WORKER_ENGINE=grok
XAI_API_KEY=xai-your-api-key-here
```

### 3. Muteki Web Engine Core (`vendor/muteki/apps/web/server.py`)
Muteki's underlying FastAPI backend brokers the event bus and manages sandboxed worker execution while MUTeki-Evolve handles high-level strategic reasoning.

---

## 🐣 Simplified How-It-Works Breakdown

| Step | Component | What Happens |
|---|---|---|
| **1. Strategy Generation** | `orchestration/orchestrator.py` | Formulates strategic priorities (e.g. *surface mapping* or *SQL injection verification*). |
| **2. Payload Translation** | `muteki_adapter/translator.py` | Translates the strategy into a Muteki Challenge contract payload. |
| **3. Swarm Execution** | `tools/grok_cli.py` & `vendor/muteki` | Muteki spawns Grok workers to execute target tool calls and HTTP requests. |
| **4. Event Normalization** | `muteki_adapter/event_normalizer.py` | Raw SSE event streams are normalized into structured `Evidence` cards. |
| **5. Evolution & Scoring** | `app/models.py` | Evaluates progress (0–100%) and generates the evolved strategy for the next round. |

---

## 🚀 Quickstart Guide

### Step 1: Clone & Environment Setup

```bash
# 1. Clone repository
git clone https://github.com/shamitha311/MUTeki-Evolve-Foundation.git
cd MUTeki-Evolve-Foundation

# 2. Create Python 3.13 virtual environment
uv venv --python 3.13
source .venv/bin/activate

# 3. Install project dependencies
uv pip install -e .
```

### Step 2: Configure Secrets (`.env`)

```bash
cp .env.example .env
```
Edit `.env`:
```env
MUTEKI_MODE=real
MUTEKI_WORKER_ENGINE=grok
XAI_API_KEY=xai-your-api-key-here
```

### Step 3: Run the Custom React UI Dashboard

```bash
cd artifacts/muteki-evolve
npm install
npx vite --host 0.0.0.0 --port 5173
```
Open **`http://localhost:5173`** in your browser to access the MUTeki-Evolve Command Center!

### Step 4: Run the Backend Strategy Evolution Engine

In a new terminal tab:
```bash
cd ~/MUTeki-Evolve-Foundation
source .venv/bin/activate
export PYTHONPATH=".:vendor/muteki"

# Run A/B integration acceptance test (5/5 gates pass)
python integration/run_real.py

# Run autonomous strategy evolution orchestrator
python -m orchestration.orchestrator
```

---

## 🧪 Verification & Test Suite

MUTeki-Evolve comes with **293/293 passing unit & integration tests** and **5/5 passed execution gates**:

```bash
export PYTHONPATH=".:vendor/muteki"
python -m pytest tests/ --tb=short
```

```text
293 passed, 0 failed in 0.90s
5/5 Integration Gates Passed
```

---

## 📁 Repository Structure

```text
MUTeki-Evolve-Foundation/
├── app/                      # Domain models (Strategy, Evidence, SandboxTarget)
├── muteki_adapter/           # RealMutekiAdapter, translator, event normalizer
├── orchestration/            # Closed-loop MTASA evolution engine & target registry
├── artifacts/muteki-evolve/  # High-performance React + Vite + Tailwind dashboard
├── bin/                      # Engine CLI bridges (bin/grok, bin/grok.cmd)
├── tools/                    # Zero-dependency xAI Grok API bridge (tools/grok_cli.py)
├── vendor/muteki/            # Vendored Muteki multi-agent swarm core
├── integration/              # 5-gate A/B strategy acceptance test runner
└── tests/                    # Automated unit & integration test suite
```

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for details.
