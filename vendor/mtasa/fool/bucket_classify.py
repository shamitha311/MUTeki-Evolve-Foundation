from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


_BUCKET_NAMES = {
    "tiny_seed42", "small_seed100",
    "medium_seed201", "medium_seed202", "medium_seed203",
    "large_seed301", "large_seed302",
    "low_willingness_seed501", "scarce_couriers_seed401",
    "high_noise_seed601",
}


def parse_bucket_scores(report_path: Path) -> dict[str, float]:
    """Extract {bucket_name: score} from a Genius TXT report.

    A bucket block is a known bucket name on its own line followed by a numeric
    line. Unknown names are skipped silently; malformed numbers are skipped too.
    """
    if not report_path.exists():
        return {}
    out: dict[str, float] = {}
    lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        name = line.strip()
        if name not in _BUCKET_NAMES:
            continue
        if i + 1 >= len(lines):
            continue
        try:
            out[name] = float(lines[i + 1].strip())
        except ValueError:
            continue
    return out


@dataclass
class RoundOutcome:
    """Result of classifying one round against per-bucket incumbents.

    label values: "baseline" | "improved" | "neutral" | "regressed" | "catastrophic"
    """

    label: str
    bucket_replacements: set[str] = field(default_factory=set)
    broken_buckets: set[str] = field(default_factory=set)
    bucket_deltas: dict[str, float] = field(default_factory=dict)


def classify_round_bucketed(
    *,
    new_scores: dict[str, float],
    bucket_incumbents: dict[str, float],
    target_buckets: list[str],
    band_rel: float = 0.003,
) -> RoundOutcome:
    """Classify per-bucket. Penalty score: lower is better.

    Two thresholds, deliberately separated:
      - bucket_replacements: STRICT (new < incumbent). Champion file is the
        source of truth for "桶下界" (theoretical floor); it must be monotone
        in real score, not in "beat the band". Without this, sub-band wins
        accumulate while champions stay stale and the floor sits above the
        average a single solver can actually reach.
      - improved/regressed label: BANDED (new < incumbent - band / new >
        incumbent + band). Keeps outcome stable against ~0.3% noise so the
        LLM doesn't chase ±0.5pt drift as if it were progress.
    """
    replacements: set[str] = set()
    label_improvements: set[str] = set()
    broken: set[str] = set()
    deltas: dict[str, float] = {}

    for bucket, new in new_scores.items():
        incumbent = bucket_incumbents.get(bucket)
        if incumbent is None:
            replacements.add(bucket)
            label_improvements.add(bucket)
            deltas[bucket] = 0.0
            continue
        deltas[bucket] = new - incumbent
        band = abs(incumbent) * band_rel
        if new < incumbent:
            replacements.add(bucket)
        if new < incumbent - band:
            label_improvements.add(bucket)
        elif new > incumbent + band:
            broken.add(bucket)

    if not bucket_incumbents:
        return RoundOutcome(
            label="baseline",
            bucket_replacements=replacements,
            bucket_deltas=deltas,
        )

    for bucket, new in new_scores.items():
        inc = bucket_incumbents.get(bucket)
        if inc is not None and new > inc * 1.5:
            return RoundOutcome(
                label="catastrophic",
                bucket_replacements=replacements,
                broken_buckets=broken,
                bucket_deltas=deltas,
            )

    if broken:
        return RoundOutcome(
            label="regressed",
            bucket_replacements=replacements,
            broken_buckets=broken,
            bucket_deltas=deltas,
        )

    if label_improvements:
        return RoundOutcome(
            label="improved",
            bucket_replacements=replacements,
            broken_buckets=broken,
            bucket_deltas=deltas,
        )

    return RoundOutcome(
        label="neutral",
        bucket_replacements=replacements,
        broken_buckets=broken,
        bucket_deltas=deltas,
    )
