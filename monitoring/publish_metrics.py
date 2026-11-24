"""
NorthStar — publish model-quality and business metrics to CloudWatch (Lab 6 Task 1).

Runs from a laptop with the repo's .venv, after a drift job has completed:

    .venv/bin/python monitoring/publish_metrics.py
    .venv/bin/python monitoring/publish_metrics.py --job nsc-evidently-1795492367
    .venv/bin/python monitoring/publish_metrics.py --dry-run

WHAT IT PUBLISHES (namespace NorthStar/ChurnModel, dimension EndpointName)

  Model-quality layer, from one drift job's drift_violations.json:
    DriftDetected        1 if any monitored feature drifted, else 0
    ViolationCount       number of drifted features
    DriftPSI             per-feature PSI statistic   (+ dimension Feature)
    DriftKSPValue        per-feature KS p-value      (+ dimension Feature)
    DriftJSD             per-feature Jensen-Shannon  (+ dimension Feature)
  These are stamped with the publish time, so alarms evaluate them at once.

  Business layer, from the variant's data capture:
    ChurnAlertVolume            predictions scoring >= --alert-threshold, per UTC day
    ChurnAlertVolumeChangePct   that day vs the mean of the prior days in the
                                capture (up to 7). Skipped when there is no
                                prior day or the prior mean is 0.
  Every UTC day found in the capture gets its own datapoint, stamped at the
  end of that day (or now, for the current day). CloudWatch stores every
  datapoint it receives, so a re-run adds a second datapoint for each past day
  with the same value. Maximum is unchanged by that; Sum and SampleCount are
  not, so read and alarm on Maximum.

By default it reads the newest Completed processing job whose name starts with
--job-prefix. Pass --job to pick a specific one.
"""

import argparse
import datetime as dt
import json
import sys
from collections import Counter

import boto3

NAMESPACE = "NorthStar/ChurnModel"

# Evidently method -> CloudWatch metric name for the per-feature value.
PER_FEATURE_METRIC = {
    "psi": "DriftPSI",
    "ks": "DriftKSPValue",
    "jensenshannon": "DriftJSD",
}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--endpoint", default="northstar-churn-prod")
    ap.add_argument("--variant", default="champion",
                    help="data-capture variant prefix to count churn alerts from")
    ap.add_argument("--bucket", default=None,
                    help="data bucket (default northstar-dev-data-<account id>)")
    ap.add_argument("--job", default=None,
                    help="processing job name (default: newest Completed --job-prefix job)")
    ap.add_argument("--job-prefix", default="nsc-evidently")
    ap.add_argument("--alert-threshold", type=float, default=0.5,
                    help="a prediction at or above this score counts as a churn alert")
    ap.add_argument("--lookback-days", type=int, default=7,
                    help="max prior days averaged for ChurnAlertVolumeChangePct")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the datapoints without publishing")
    return ap.parse_args()


def newest_completed_job(sm, prefix):
    resp = sm.list_processing_jobs(NameContains=prefix, StatusEquals="Completed",
                                   SortBy="CreationTime", SortOrder="Descending",
                                   MaxResults=1)
    jobs = resp.get("ProcessingJobSummaries", [])
    if not jobs:
        sys.exit(f"No Completed processing job contains {prefix!r}. Run "
                 "monitoring/run_evidently_job.py first, or pass --job.")
    return jobs[0]["ProcessingJobName"]


def violations_uri(sm, job):
    desc = sm.describe_processing_job(ProcessingJobName=job)
    if desc["ProcessingJobStatus"] != "Completed":
        sys.exit(f"{job} is {desc['ProcessingJobStatus']}, not Completed.")
    outputs = desc["ProcessingOutputConfig"]["Outputs"]
    return outputs[0]["S3Output"]["S3Uri"].rstrip("/") + "/drift_violations.json"


def read_s3_json(s3, uri):
    bucket, key = uri[len("s3://"):].split("/", 1)
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def drift_datapoints(summary, endpoint, now):
    dims = [{"Name": "EndpointName", "Value": endpoint}]
    points = [
        {"MetricName": "DriftDetected", "Dimensions": dims, "Timestamp": now,
         "Value": 1.0 if summary["drift_detected"] else 0.0, "Unit": "Count"},
        {"MetricName": "ViolationCount", "Dimensions": dims, "Timestamp": now,
         "Value": float(summary["violation_count"]), "Unit": "Count"},
    ]
    for feature, d in summary["per_feature"].items():
        name = PER_FEATURE_METRIC.get(d["method"])
        if name is None:
            print(f"  skip {feature}: no metric name for method {d['method']!r}")
            continue
        points.append({
            "MetricName": name, "Timestamp": now, "Value": float(d["value"]),
            "Unit": "None",
            "Dimensions": dims + [{"Name": "Feature", "Value": feature}],
        })
    return points


def count_alerts_per_day(s3, bucket, endpoint, variant, threshold):
    """Return Counter{date: number of predictions >= threshold} from capture."""
    prefix = f"datacapture/{endpoint}/{variant}/"
    per_day, total = Counter(), 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".jsonl"):
                continue
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode()
            for line in body.splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                day = dt.date.fromisoformat(rec["eventMetadata"]["inferenceTime"][:10])
                per_day.setdefault(day, 0)  # a day with zero alerts still counts
                raw = rec["captureData"]["endpointOutput"]["data"]
                for tok in raw.replace("\n", ",").split(","):
                    if tok.strip():
                        total += 1
                        if float(tok) >= threshold:
                            per_day[day] += 1
    if total == 0:
        sys.exit(f"No captured predictions under s3://{bucket}/{prefix}")
    return per_day


def business_datapoints(per_day, endpoint, lookback, now):
    dims = [{"Name": "EndpointName", "Value": endpoint}]
    points = []
    for day in sorted(per_day):
        end_of_day = dt.datetime.combine(day, dt.time(23, 59, 59), dt.timezone.utc)
        ts = min(end_of_day, now)
        points.append({"MetricName": "ChurnAlertVolume", "Dimensions": dims,
                       "Timestamp": ts, "Value": float(per_day[day]), "Unit": "Count"})
        prior = [per_day[d] for d in per_day
                 if dt.timedelta(0) < day - d <= dt.timedelta(days=lookback)]
        if not prior:
            print(f"  {day}: no prior day in capture, ChangePct skipped")
            continue
        mean = sum(prior) / len(prior)
        if mean == 0:
            print(f"  {day}: prior mean is 0, ChangePct skipped")
            continue
        pct = 100.0 * (per_day[day] - mean) / mean
        points.append({"MetricName": "ChurnAlertVolumeChangePct", "Dimensions": dims,
                       "Timestamp": ts, "Value": pct, "Unit": "Percent"})
    return points


def main():
    args = parse_args()
    session = boto3.session.Session()
    sm, s3, cw = session.client("sagemaker"), session.client("s3"), session.client("cloudwatch")
    bucket = args.bucket or (
        "northstar-dev-data-" + session.client("sts").get_caller_identity()["Account"])
    now = dt.datetime.now(dt.timezone.utc)

    job = args.job or newest_completed_job(sm, args.job_prefix)
    uri = violations_uri(sm, job)
    summary = read_s3_json(s3, uri)
    print(f"drift job : {job}\nsummary   : {uri}")
    if summary.get("underpowered_window"):
        print(f"WARNING: {job} ran on {summary['captured_records']} records and is "
              "flagged underpowered; its drift result is not trustworthy.")

    per_day = count_alerts_per_day(s3, bucket, args.endpoint, args.variant,
                                   args.alert_threshold)
    points = (drift_datapoints(summary, args.endpoint, now)
              + business_datapoints(per_day, args.endpoint, args.lookback_days, now))

    print(f"\n{'metric':<27} {'feature':<26} {'timestamp (UTC)':<20} {'value':>10}")
    print("-" * 86)
    for p in points:
        feat = next((d["Value"] for d in p["Dimensions"] if d["Name"] == "Feature"), "")
        print(f"{p['MetricName']:<27} {feat:<26} "
              f"{p['Timestamp'].strftime('%Y-%m-%d %H:%M:%S'):<20} {p['Value']:>10.4f}")

    if args.dry_run:
        print("\n--dry-run: nothing published")
        return 0
    for i in range(0, len(points), 1000):  # PutMetricData accepts 1000 per call
        cw.put_metric_data(Namespace=NAMESPACE, MetricData=points[i:i + 1000])
    print(f"\npublished {len(points)} datapoints to {NAMESPACE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
