"""Compare the incumbent and candidate outputs of one churn batch run.

Reads both joined outputs (one "customer_id,score" line per customer), matches
them by customer_id, and writes docs/lab5-batch-comparison.md with both score
distributions and the disagreement rate at the 0.5 churn cut-off. The rollback
threshold is read from deployment/configs/rollback-alarm.json, so the checker,
the CloudWatch alarm and the deployment plan all share one number.

Normally run by scripts/churn-batch-compare.sh:
    python scripts/churn_batch_compare.py WORKDIR RUN_ID \
        INCUMBENT_JOB INCUMBENT_VERSION CANDIDATE_JOB CANDIDATE_VERSION
WORKDIR holds the downloaded outputs in incumbent/ and candidate/.
"""
import glob
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

CUTOFF = 0.5
ALARM_CFG = "deployment/configs/rollback-alarm.json"
REPORT = "docs/lab5-batch-comparison.md"


def load_scores(work, tag):
    files = sorted(glob.glob(f"{work}/{tag}/**/*.out", recursive=True))
    if not files:
        sys.exit(f"{tag}: no .out files downloaded")
    df = pd.concat(
        pd.read_csv(f, header=None, names=["customer_id", "score"],
                    dtype={"customer_id": str})
        for f in files)
    if df["customer_id"].duplicated().any():
        sys.exit(f"{tag}: duplicate customer_id in transform output")
    return df.set_index("customer_id")["score"]


def main():
    work, run_id, job_inc, v_inc, job_cand, v_cand = sys.argv[1:7]
    alarm = json.load(open(ALARM_CFG))
    if alarm["ComparisonOperator"] != "GreaterThanThreshold":
        sys.exit("this checker implements GreaterThanThreshold only")
    threshold = float(alarm["Threshold"])

    both = pd.concat({"inc": load_scores(work, "incumbent"),
                      "cand": load_scores(work, "candidate")}, axis=1, join="outer")
    unmatched = int(both.isna().any(axis=1).sum())
    if unmatched:
        sys.exit(f"{unmatched} customers were scored by only one model")

    n = len(both)
    churn_inc = both["inc"] >= CUTOFF
    churn_cand = both["cand"] >= CUTOFF
    disagree = churn_inc != churn_cand
    pct = 100.0 * disagree.mean()
    breached = pct > threshold

    stats = [("min", "min"), ("10th percentile", 0.10), ("25th percentile", 0.25),
             ("median", 0.50), ("75th percentile", 0.75), ("90th percentile", 0.90),
             ("max", "max"), ("mean", "mean")]

    def stat(series, how):
        if isinstance(how, float):
            return series.quantile(how)
        return getattr(series, how)()

    edges = np.linspace(0.0, 1.0, 11)
    hist_inc, _ = np.histogram(both["inc"], bins=edges)
    hist_cand, _ = np.histogram(both["cand"], bins=edges)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# Lab 5 Batch Comparison: incumbent v{v_inc} vs candidate v{v_cand}",
        "",
        f"Run `{run_id}`, generated {now} by `scripts/churn-batch-compare.sh`. The numbers are produced by the script; re-run it rather than editing them.",
        "",
        "## Jobs",
        "",
        "| Role | Model package | Transform job |",
        "|---|---|---|",
        f"| Incumbent | northstar-churn-models/{v_inc} | `{job_inc}` |",
        f"| Candidate | northstar-churn-models/{v_cand} | `{job_cand}` |",
        "",
        f"Customers scored by both models, matched by `customer_id`: **{n}**",
        "",
        "## Score distributions",
        "",
        f"| Statistic | v{v_inc} | v{v_cand} |",
        "|---|---|---|",
    ]
    for label, how in stats:
        lines.append(f"| {label} | {stat(both['inc'], how):.4f} | {stat(both['cand'], how):.4f} |")
    lines += [
        "",
        "## Score histogram (customers per score band)",
        "",
        f"| Score band | v{v_inc} | v{v_cand} |",
        "|---|---|---|",
    ]
    for lo, hi, a, b in zip(edges[:-1], edges[1:], hist_inc, hist_cand):
        lines.append(f"| {lo:.1f} to {hi:.1f} | {a} | {b} |")
    lines += [
        "",
        f"## Disagreement at the {CUTOFF} churn cut-off",
        "",
        f"A customer is predicted to churn when their score is {CUTOFF} or higher. "
        "The models disagree on a customer when exactly one of them predicts churn.",
        "",
        "| Measure | Customers | Share |",
        "|---|---|---|",
        f"| Predicted churn by v{v_inc} | {int(churn_inc.sum())} | {100 * churn_inc.mean():.2f}% |",
        f"| Predicted churn by v{v_cand} | {int(churn_cand.sum())} | {100 * churn_cand.mean():.2f}% |",
        f"| Churn by v{v_inc} only | {int((churn_inc & ~churn_cand).sum())} | |",
        f"| Churn by v{v_cand} only | {int((churn_cand & ~churn_inc).sum())} | |",
        f"| **Disagreement** | **{int(disagree.sum())}** | **{pct:.2f}%** |",
        "",
        "## Rollback trigger",
        "",
        f"Defined in `{ALARM_CFG}`: `{alarm['MetricName']}` > {threshold:g} ({alarm['Unit']}). "
        f"This run published {pct:.2f}.",
        "",
        (f"**Result: BREACHED.** Roll back with `bash deployment/configs/rollback-action.sh {v_cand}`."
         if breached else
         f"**Result: within threshold.** The candidate may be promoted with `bash scripts/churn-batch-promote.sh {v_cand}`."),
        "",
    ]
    with open(REPORT, "w") as f:
        f.write("\n".join(lines))

    with open(f"{work}/disagreement_pct", "w") as f:
        f.write(f"{pct:.4f}")
    with open(f"{work}/verdict", "w") as f:
        f.write("BREACHED" if breached else "WITHIN")
    print(f"customers matched: {n}   disagreement: {pct:.2f}%   threshold: {threshold:g}%   "
          f"{'BREACHED' if breached else 'within threshold'}")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
