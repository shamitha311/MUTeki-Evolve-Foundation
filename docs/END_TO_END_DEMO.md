# MUTeki-Evolve — End-to-End Autonomous Investigation Demo

> [!NOTE]
> **ENVIRONMENT & DEMO MODE NOTICE (READ FIRST)**
> As documented in `docs/UPSTREAM_NOTES.md` and `docs/ORCHESTRATION.md`, container-backed execution is disabled in this environment (`REPLIT_DISABLE_DOCKER`), and no live Muteki LLM credential or external sandbox target is configured.
> 
> Therefore, this end-to-end demonstration uses **MOCK mode (`MODE=mock`)**.
> 
> The MOCK mode exercises 100% of the project-owned autonomous loop—Strategy Generation, Safety Validation, Adapter Execution, Event Normalization, Evaluation Engine Scoring, Strategy Memory, Reviewer Guidance, and Next Strategy Improvement—using deterministic upstream test fixtures. REAL mode is fully wired in code, but is unverified due to environment constraints.

---

## 1. Prerequisites

- Python 3.10+ (tested on Python 3.13)
- Pytest test runner installed (`python -m pytest`)
- Workspace set to `MUTeki-Evolve-Foundation`

---

## 2. Running the End-to-End Automated Demonstration

To run the complete automated end-to-end evolution test suite:

```bash
python -m pytest tests/orchestration/test_orchestrator.py -v
```

This test suite executes the closed-loop investigation orchestrator through a complete 3-round evolution run.

---

## 3. Programmatic End-to-End Demonstration Script

You can also run the demonstration programmatically in Python:

```python
import asyncio
from orchestration import Orchestrator, RunStatus

async def run_demo():
    orchestrator = Orchestrator()
    
    # 1. Trusted target resolution & run creation
    print("Starting Autonomous Evolution Run against 'trusted-demo-target'...")
    state = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Investigate local target and verify security parameters.",
        run_id="demo-run-001",
        max_iterations=3,
        mode="mock",
    )
    
    print(f"\nFinal Run Status: {state.status.value}")
    print(f"Termination Reason: {state.termination_reason.value}")
    print(f"Best Score: {state.best_score} ({state.latest_score.progress_level})")
    print(f"Total Iterations: {state.current_iteration}")
    
    # 2. Strategy & Score Evolution Breakdown
    print("\n--- Strategy Evolution Lineage ---")
    for record in state.history:
        print(f"\nIteration {record.iteration}:")
        print(f"  Strategy Revision: {record.strategy.revision} (Parent: {record.strategy.parent_revision})")
        print(f"  Priorities: {record.strategy.priorities}")
        print(f"  Result Solved: {record.result.solved}")
        print(f"  Score: {record.score.progress_score} ({record.score.progress_level})")
        print(f"  Reasons: {record.score.reasons[0] if record.score.reasons else 'N/A'}")

if __name__ == "__main__":
    asyncio.run(run_demo())
```

---

## 4. Expected Demonstration Output

When executed, the system demonstrates true iterative strategy evolution:

### Round 1: Surface Reconnaissance
- **Strategy**: Revision 1 (Priorities: `['reconnaissance', 'evidence collection']`)
- **Adapter Result**: Surface discovered (`solved=False`)
- **Evaluator**: Score `28.0` (`progress_level="reconnaissance"`)
- **Reviewer**: Recommends hypothesis testing and evidence correlation based on discovered attack surface.

### Round 2: Deep Analysis & Correlation
- **Strategy**: Revision 2 (Parent: 1, Priorities: `['evidence correlation', 'hypothesis testing']`)
- **Adapter Result**: Strong evidence found (`solved=False`)
- **Evaluator**: Score `72.0` (`progress_level="strong evidence"`)
- **Reviewer**: Recommends verification of identified vulnerability paths.

### Round 3: Success Verification
- **Strategy**: Revision 3 (Parent: 2, Priorities: `['verification', 'clear success evidence']`)
- **Adapter Result**: Solved condition verified (`solved=True`)
- **Evaluator**: Score `100.0` (`progress_level="verified success"`)
- **Termination**: Closed loop halts immediately upon `SOLVED` without executing further iterations.

---

## 5. Security Boundary Verification

To verify that security boundaries reject unauthorized targets or malicious strategy content:

```python
# Untrusted target ID -> Rejection before execution
try:
    await orchestrator.run_investigation(
        target_id="untrusted-target-ip",
        objective="Hacking target",
    )
except Exception as e:
    print(f"Correctly rejected untrusted target: {e}")

# Malicious strategy payload -> Fail-closed validation rejection
from app.validation import approve_strategy
try:
    target = orchestrator.registry.resolve("trusted-demo-target")
    approve_strategy(target, {"objective": "Test", "context": {"command": "rm -rf /"}}, orchestrator.registry)
except Exception as e:
    print(f"Correctly rejected malicious strategy: {e}")
```
