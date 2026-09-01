# Genius Module

Genius simulates an online judge.

Responsibilities:

1. Load case datasets.
2. Execute a submitted solver script.
3. Validate output structure and basic constraints.
4. Score each case with fixed mode: official_like_latest.
5. Emit standard TXT report.
6. Record hash, runtime, and validity metadata.

Hard runtime constraints:

1. Solver is executed by Python 3.6 (`python3.6`) by default.
2. Solver source file must be <= 100KB.
3. Solver top-level imports are restricted to `import time`, `import random`, `import heapq`, `from collections import defaultdict`, and `from typing import List, Tuple, Set, Dict, Optional, Iterable`. `from __future__ import ...` and other top-level imports are rejected before scoring.
4. Each case has a hard runtime cap of 10 seconds on the online judge. Local Genius runs ~3× slower than the online judge, so the local default `max_case_seconds` is **30s** (`DEFAULT_CASE_TIMEOUT_SEC` in `solver_executor.py`); a solver finishing within 30s locally is expected to fit the 10s online budget.
5. Output format errors are reported explicitly.

Failure signaling:

- Python 3.6 missing/incompatible: explicit incompatibility error.
- File too large: explicit size-limit error.
- Unsupported top-level solver import: explicit preflight error.
- Output malformed: explicit format_error in case message.
- Single-case timeout: explicit timeout warning when a case exceeds the configured `max_case_seconds` (local default 30s ≈ online 10s budget).

Two entry methods:

- CLI: `python genius/genius_judge.py ...`
- Python API: `from genius.genius_judge import run_judge`

Scoring mode policy:

- Only one scoring mode is allowed: official_like_latest.
- The mode is implemented by case-table evaluation (score + willingness recursive backup aggregation + uncovered-task penalty), aligned with autosolver evaluator behavior.
