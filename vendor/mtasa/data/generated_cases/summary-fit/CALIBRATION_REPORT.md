# SUMMARY Profile Calibration Report

## Recommendation

Use two offline datasets together:

1. `data/summary-fit/`: primary screen for decoded online structure and
   classifier behavior.
2. `data/calibrated-online-fit/`: secondary screen for consistency with
   historical online score patterns.

Neither dataset is a recovered copy of the inaccessible official inputs.

## Why Two Tracks

`data/SUMMARY.md` contains decoded 19-dimensional online profiles. These are
stronger than score-only fitting for new structural algorithms. The earlier
`calibrated-online-fit` dataset intentionally uses historical score ratios, so
it is better at reproducing known score ordering but more prone to overfitting
past solver behavior.

Historical solver validation:

| Dataset | Mean rank correlation | Mean shape correlation | Normalized shape error |
|---|---:|---:|---:|
| `summary-fit` | 0.9273 | 0.8871 | 0.3821 |
| `calibrated-online-fit` | 0.9636 | 0.9229 | 0.3569 |
| `informs-seeds2.0` | 0.9030 | 0.7112 | 0.8763 |

Lower normalized shape error is better. Both new datasets are substantially
more useful than `informs-seeds2.0`.

## Fitted Signals

The generator adjusts feasible dimensions from `data/calibrated/`:

- task, courier and candidate counts;
- combo ratio and bundle size;
- score mean, median and standard deviation;
- willingness mean, standard deviation and tails;
- Pearson correlation between score and willingness;
- resulting Formula-A mean.

The three medium cases are no longer identical:

- `medium201`: normal profile;
- `medium202`: heavier low-willingness tail;
- `medium203`: stronger negative score-willingness correlation.

`large301` is copied from `data/official/large_seed301.txt`, because that gold
input is already available locally.

## Irreducible Inconsistencies

Some rounded probe values cannot all hold after deduplication:

- tiny: `6` tasks, `12` couriers and bundle size at most `2` allow at most
  `252` unique rows, not approximately `450`;
- medium: requiring same-courier solo support for every combo row imposes at
  least `1860` solo rows, conflicting with `27750` total rows and approximate
  `0.95` combo ratio.

The generated set prioritizes exact observations and feasible constraints.

## Commands

Generate:

```bash
python3 fit_summary_online_proxy.py
```

Validate against historical solvers:

```bash
python3 validate_online_proxy.py
```
