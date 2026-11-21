"""
NorthStar — launch the Evidently drift analysis as a SageMaker Processing Job.

Lab 6 Task 1. Run this from your laptop; it launches evidently_drift.py inside
a managed container and returns when the job finishes.

    python monitoring/run_evidently_job.py \
        --bucket northstar-dev-data-<account> \
        --role-arn arn:aws:iam::<account>:role/northstar-dev-ModelMonitorExecution \
        --endpoint northstar-churn-prod \
        --variant champion

WHY ml.t3.medium IS FINE HERE
-----------------------------
Your account's processing quota is ZERO for every non-burstable instance type.
Of 126 processing instance types, exactly three have a non-zero AWS default and
all three are burstable: ml.t3.medium (4), ml.t3.large (4), ml.t3.xlarge (2).

SageMaker Model Monitor's analyzer is a Spark container and OOMs on t3.medium's
4 GB -- it takes 13 min 43 s to fail and then blames your data, not the instance.
Evidently is pandas. Measured on this exact job: 2 min 08 s of billable time on
ml.t3.medium, 10,000 baseline rows against 800 captured records. You do not need
t3.large and you do not need a quota increase.

WHY THE VERSION IS PINNED
-------------------------
Evidently's API broke at 0.7: column_mapping was replaced by DataDefinition, and
a bare DataFrame no longer works -- you must wrap it in a Dataset. Almost every
tutorial you will find online predates this and fails with an ImportError. Pin
the version so your lab does not break when upstream ships 0.8.
"""

import argparse
import json
import sys
import time

import boto3

# Python 3.12 container. Evidently requires Python >= 3.10, so the older
# sklearn "1.2-1" image will NOT work -- this tag is load-bearing.
IMAGE = ("683313688378.dkr.ecr.us-east-1.amazonaws.com/"
         "sagemaker-scikit-learn:1.4-2-py312-cpu-py3")
EVIDENTLY_VERSION = "0.7.21"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--role-arn", required=True)
    ap.add_argument("--endpoint", default="northstar-churn-prod")
    ap.add_argument("--variant", default="champion",
                    help="Capture is partitioned per variant. Pick ONE and "
                         "say which in your writeup.")
    ap.add_argument("--instance", default="ml.t3.medium")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--wait", action="store_true", default=True)
    args = ap.parse_args()

    sm = boto3.client("sagemaker", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)

    job = f"nsc-evidently-{int(time.time())}"
    b = args.bucket

    # Fail fast and for free: the starter kit ships drift_direction() as a
    # stub. Launched as-is, the job pays for the pip install and the analysis
    # and then dies on NotImplementedError inside the container.
    with open("monitoring/evidently_drift.py") as fh:
        if "raise NotImplementedError" in fh.read():
            sys.exit("FATAL: drift_direction() in monitoring/evidently_drift.py "
                     "is still the starter-kit stub. Implement it before "
                     "launching a billable job.")

    # Ship the analysis script to S3 so the container can mount it. It goes
    # under monitoring/ because that is the only prefix (with datacapture/)
    # the ModelMonitorExecution role can read.
    code_prefix = f"monitoring/code/{job}/"
    s3.upload_file("monitoring/evidently_drift.py", b,
                   f"{code_prefix}evidently_drift.py")

    capture_uri = f"s3://{b}/datacapture/{args.endpoint}/{args.variant}/"

    # Fail fast and cheaply: an empty capture prefix costs you a full job
    # otherwise, and the error it produces names the wrong cause.
    probe = s3.list_objects_v2(Bucket=b,
                               Prefix=f"datacapture/{args.endpoint}/{args.variant}/",
                               MaxKeys=1)
    if probe.get("KeyCount", 0) == 0:
        sys.exit(f"FATAL: no captured data at {capture_uri}\n"
                 "Capture is written asynchronously and lags several minutes "
                 "behind your first invocation. Invoke the endpoint, wait, and "
                 "confirm objects exist before launching a billable job.")

    def s3_in(name, uri, local):
        return {"InputName": name,
                "S3Input": {"S3Uri": uri, "LocalPath": local,
                            "S3DataType": "S3Prefix", "S3InputMode": "File"}}

    sm.create_processing_job(
        ProcessingJobName=job,
        RoleArn=args.role_arn,
        AppSpecification={
            "ImageUri": IMAGE,
            # pip install at job start. The container has egress by default;
            # you do NOT need a NAT gateway for this.
            "ContainerEntrypoint": [
                "/bin/bash", "-c",
                f"pip install --quiet evidently=={EVIDENTLY_VERSION} && "
                "python /opt/ml/processing/input/code/evidently_drift.py",
            ],
        },
        ProcessingResources={"ClusterConfig": {
            "InstanceCount": 1, "InstanceType": args.instance,
            "VolumeSizeInGB": 20}},
        StoppingCondition={"MaxRuntimeInSeconds": 3600},
        ProcessingInputs=[
            s3_in("code", f"s3://{b}/{code_prefix}",
                  "/opt/ml/processing/input/code"),
            s3_in("baseline", f"s3://{b}/monitoring/baseline/",
                  "/opt/ml/processing/input/baseline"),
            s3_in("capture", capture_uri,
                  "/opt/ml/processing/input/capture"),
        ],
        ProcessingOutputConfig={"Outputs": [{
            "OutputName": "report",
            "S3Output": {"S3Uri": f"s3://{b}/monitoring/reports/{job}/",
                         "LocalPath": "/opt/ml/processing/output",
                         "S3UploadMode": "EndOfJob"}}]},
    )
    print(f"launched {job} on {args.instance}")
    print(f"reports  s3://{b}/monitoring/reports/{job}/")

    if not args.wait:
        return 0

    while True:
        d = sm.describe_processing_job(ProcessingJobName=job)
        status = d["ProcessingJobStatus"]
        if status in ("Completed", "Failed", "Stopped"):
            break
        print(f"  {status} ...")
        time.sleep(20)

    if status != "Completed":
        print(f"FAILED: {d.get('FailureReason')}")
        return 1

    billable = (d["ProcessingEndTime"] - d["ProcessingStartTime"]).total_seconds()
    print(f"{status} in {billable:.0f}s billable")

    # Pull the summary back so you can publish your drift metric from here.
    key = f"monitoring/reports/{job}/drift_violations.json"
    body = s3.get_object(Bucket=b, Key=key)["Body"].read()
    summary = json.loads(body)
    print(json.dumps(summary, indent=2)[:600])
    print(f"\ndrift_detected={summary['drift_detected']} "
          f"violations={summary['violation_count']}")
    print(f"\nNext: publish your drift metric to CloudWatch from your laptop, "
          f"using s3://{b}/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
