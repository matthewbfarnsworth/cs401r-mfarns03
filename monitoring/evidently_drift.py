"""
NorthStar — Evidently drift analysis, Lab 6 Task 1.

Runs INSIDE a SageMaker Processing Job (script mode, sklearn container).
Not run on your laptop. See monitoring/run_evidently_job.py for the launcher.

WHY EVIDENTLY AND NOT SAGEMAKER MODEL MONITOR
---------------------------------------------
SageMaker Model Monitor SCHEDULES are closed to new AWS accounts:
CreateMonitoringSchedule and CreateDataQualityJobDefinition both return
"This operation is in maintenance mode and is not available to new customers."
Every student account is new, so no student can create one.

Evidently is an open-source Python library that does the same analysis with
no managed control plane to be closed out of. It is pandas, not Spark, so it
fits in ml.t3.medium (4 GB) -- the cheapest instance type that has any default
processing quota at all. Model Monitor's Spark analyzer needs ml.t3.large.

WHAT THIS READS
---------------
  /opt/ml/processing/input/baseline/   the reference distribution (CSV)
  /opt/ml/processing/input/capture/    SageMaker data capture (JSONL)

WHAT THIS WRITES
----------------
  /opt/ml/processing/output/drift_report.html    human-readable
  /opt/ml/processing/output/drift_report.json    full Evidently output
  /opt/ml/processing/output/drift_violations.json  the summary you alarm on

THE TRAP THAT WILL GET YOU
--------------------------
Evidently returns a DIFFERENT KIND OF NUMBER depending on the test:

    psi  -> a STATISTIC.  Drift when value >  threshold.  Rises with drift.
    ks   -> a P-VALUE.    Drift when value <  threshold.  FALLS with drift.

Measured on synthetic data (n_ref=2000, n_cur=800, threshold psi 0.2 / ks 0.05):

    mean shift |  ks (p-value)  |  psi (statistic)
    -----------+----------------+-----------------
             0 |       0.272552 |           0.0235
             2 |       0.000000 |           0.0472
             5 |       0.000000 |           0.2757
            15 |       0.000000 |           2.3176

If you loop over mixed tests with a single `if value > threshold`, KS is
inverted and reports NO DRIFT on maximally drifted data, because 0.0 is not
greater than 0.05. It fails silently and looks like a clean run. That is why
drift_direction() below is a function and not an inline comparison.
"""

import argparse
import json
import os
import sys

import pandas as pd

from evidently import Dataset, DataDefinition, Report
from evidently.metrics import ValueDrift, DriftedColumnsCount

# The 11 features the ENDPOINT receives, in payload order. This must match
# FEATURE_COLUMNS in the training skeleton. It is deliberately NOT the full
# training frame: churn_label is the target and churn_risk_score is the Lab 3
# recency baseline, and neither is an input. A baseline built over 12 or 13
# columns fails against an 11-feature endpoint with schema noise that never
# names its real cause.
FEATURE_COLUMNS = [
    "days_since_last_purchase",
    "customer_tenure_days",
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    "category_diversity_score",
    "online_to_store_ratio",
]

# Per-feature test plan: {feature: (Evidently method, threshold)}. This is a
# working starting point that matches the reference run in the handout. Task 2
# asks you to choose the test and threshold for each feature you monitor and
# justify the choice -- edit this to match your plan.
DRIFT_PLAN = {
    "days_since_last_purchase": ("psi", 0.2),
    "purchase_frequency_30d":   ("psi", 0.2),
    "avg_order_value":          ("psi", 0.2),
    "category_diversity_score": ("ks", 0.05),
    "total_spend_90d":          ("ks", 0.05),
}

IN_BASE = "/opt/ml/processing/input/baseline"
IN_CAP = "/opt/ml/processing/input/capture"
OUT = "/opt/ml/processing/output"


def drift_direction(method: str, value: float, threshold: float) -> bool:
    """Return True if this value means DRIFT, respecting each test's semantics.

    TODO (Lab 6 Task 1): implement this function.

    `method` is the Evidently test name from DRIFT_PLAN ("psi", "ks", ...),
    `value` is the number Evidently returned for that feature, and `threshold`
    is the threshold from DRIFT_PLAN. Return True when the feature has drifted.

    Read "THE TRAP THAT WILL GET YOU" at the top of this file before you write
    it. Your implementation must handle every test you put in DRIFT_PLAN.
    Test it locally on the synthetic numbers in that table before you spend
    money on a processing job.

    run_evidently_job.py refuses to launch while this function still raises
    NotImplementedError.
    """
    raise NotImplementedError(
        "drift_direction() is not implemented -- see the TODO in "
        "monitoring/evidently_drift.py")


def load_baseline(path: str) -> pd.DataFrame:
    files = [f for f in os.listdir(path) if f.endswith(".csv")]
    if not files:
        sys.exit(f"FATAL: no CSV in {path}. Did the baseline upload succeed?")
    df = pd.concat([pd.read_csv(os.path.join(path, f)) for f in files],
                   ignore_index=True)
    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        sys.exit(f"FATAL: baseline is missing {missing}. It must describe the "
                 f"11 features the ENDPOINT receives, not the training frame.")
    return df[FEATURE_COLUMNS]


def load_capture(path: str) -> pd.DataFrame:
    """Parse SageMaker data capture JSONL into a feature frame.

    Capture format, one JSON object per line:
      {"captureData": {"endpointInput":  {"data": "12,340,...", "encoding": "CSV"},
                       "endpointOutput": {"data": "0.23"}},
       "eventMetadata": {...}}

    Capture is partitioned per variant:
      datacapture/<endpoint>/<variant>/<yyyy>/<mm>/<dd>/<hh>/
    so point this at ONE variant and say which one in your writeup.

    NOTE: if you invoked the endpoint with a MULTI-ROW payload, the whole batch
    is captured as ONE string and you get a single row back per invocation.
    Invoke one row at a time for a comparison window that means anything.
    """
    rows = []
    found = 0
    for root, _, files in os.walk(path):
        for fn in files:
            if not (fn.endswith(".jsonl") or fn.endswith(".json")):
                continue
            found += 1
            with open(os.path.join(root, fn)) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        payload = rec["captureData"]["endpointInput"]["data"]
                    except (json.JSONDecodeError, KeyError):
                        continue
                    for sub in payload.strip().splitlines():
                        vals = sub.split(",")
                        if len(vals) != len(FEATURE_COLUMNS):
                            continue
                        try:
                            rows.append([float(v) for v in vals])
                        except ValueError:
                            continue   # header line or non-numeric payload

    if found == 0:
        sys.exit(f"FATAL: no capture files under {path}.\n"
                 "Data capture is written ASYNCHRONOUSLY and lags several "
                 "minutes behind your first invocation. Confirm objects exist "
                 "in S3 before launching this job -- an empty prefix produces "
                 "a confusing schema error, not a helpful 'no input' error.")
    if not rows:
        sys.exit(f"FATAL: {found} capture file(s) but 0 parseable rows with "
                 f"{len(FEATURE_COLUMNS)} features. Check your payload shape.")
    df = pd.DataFrame(rows, columns=FEATURE_COLUMNS)

    # Drop byte-identical payloads. canary_deploy_realtime.py checks the
    # traffic split by sending sample.csv 200 times, and capture records every
    # call: ~181 copies of ONE row land in the champion window. Measured on
    # the reference account 2026-11-18, those copies alone turned a clean
    # 900-row window into KS p = 0.0000 on two features -- drift that is not
    # there. Eleven continuous features make a genuine exact repeat
    # vanishingly rare, and re-scoring the same vector says nothing about the
    # population, so dedup costs nothing real.
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    if len(df) < before:
        print(f"dropped {before - len(df)} duplicate payload(s) "
              f"(smoke-test or split-check traffic); {len(df)} distinct rows kept")
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-records", type=int, default=500,
                    help="Refuse to report drift on a window smaller than this. "
                         "Small windows produce false positives on clean data.")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)

    ref_df = load_baseline(IN_BASE)
    cur_df = load_capture(IN_CAP)
    print(f"baseline : {len(ref_df):,} rows x {len(ref_df.columns)} features")
    print(f"captured : {len(cur_df):,} rows x {len(cur_df.columns)} features")

    # A small comparison window shows drift on clean data. Model Monitor had
    # the same behaviour: 60 records produced 8 violations against a baseline
    # the data actually matched. Warn rather than silently mislead.
    underpowered = len(cur_df) < args.min_records
    if underpowered:
        print(f"WARNING: only {len(cur_df)} captured records (<{args.min_records}). "
              "Drift results on a window this small are not trustworthy. "
              "Invoke the endpoint more and re-run.")

    dd = DataDefinition(numerical_columns=FEATURE_COLUMNS)
    ref = Dataset.from_pandas(ref_df, data_definition=dd)
    cur = Dataset.from_pandas(cur_df, data_definition=dd)

    metrics = [ValueDrift(column=c, method=m, threshold=t)
               for c, (m, t) in DRIFT_PLAN.items()]
    metrics.append(DriftedColumnsCount(drift_share=0.3))

    result = Report(metrics).run(cur, ref)

    result.save_html(os.path.join(OUT, "drift_report.html"))
    full = json.loads(result.json())
    with open(os.path.join(OUT, "drift_report.json"), "w") as fh:
        json.dump(full, fh, indent=2)

    # Reduce to the thing you can actually alarm on.
    violations, per_feature = [], {}
    for m in full["metrics"]:
        cfg = m.get("config", {})
        col = cfg.get("column")
        if not col or col not in DRIFT_PLAN:
            continue
        method, threshold = DRIFT_PLAN[col]
        value = float(m["value"])
        drifted = drift_direction(method, value, threshold)
        per_feature[col] = {"method": method, "threshold": threshold,
                            "value": value, "drifted": drifted}
        if drifted:
            violations.append({
                "feature": col, "method": method,
                "value": value, "threshold": threshold,
                "detail": (f"{method.upper()} {value:.4f} "
                           f"{'<' if method == 'ks' else '>'} {threshold}"),
            })

    summary = {
        "baseline_records": len(ref_df),
        "captured_records": len(cur_df),
        "features_evaluated": len(per_feature),
        "underpowered_window": underpowered,
        "drift_detected": len(violations) > 0,
        "violation_count": len(violations),
        "violations": violations,
        "per_feature": per_feature,
    }
    with open(os.path.join(OUT, "drift_violations.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\n{'feature':<28} {'test':<5} {'value':>12} {'thresh':>8}  drift")
    print("-" * 68)
    for col, d in per_feature.items():
        print(f"{col:<28} {d['method']:<5} {d['value']:>12.4f} "
              f"{d['threshold']:>8} {'  YES' if d['drifted'] else '   no'}")
    print(f"\ndrift_detected={summary['drift_detected']} "
          f"violations={len(violations)}")

    # Exit 0 even when drift is found. Drift is a RESULT, not a job failure --
    # a non-zero exit here would make the processing job look broken and would
    # hide the report you just paid for.
    return 0


if __name__ == "__main__":
    sys.exit(main())
