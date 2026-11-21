"""
NorthStar Retail - Real-Time Canary Deployment (Lab 6 starter kit)
==================================================================
Shipped in Lab 6 so you can roll a data-capture-enabled endpoint config
(see Lab 6 Prerequisite 1). This is the Lab 5 real-time reference path.

VERIFIED ON AWS across two runs on the instructor's reference account, us-east-1:
  2026-07-30  ml.m5.large, no data capture
  2026-07-31  ml.t2.medium, DATA CAPTURE ENABLED, full canary + rollback

Every timing, threshold and observed number in this file was measured, not
estimated. Data capture is now verified end to end, including the two-variant
case: objects land at the documented layout, partitioned PER VARIANT.

Lab 5 Task 1 permits Batch Transform (see canary_deploy.py) or a real-time endpoint.
This file is the real-time path: the one the auto-scaling requirement actually
exercises, and the one most students choose.

WHAT THE REFERENCE RUNS MEASURED
--------------------------------
                                              ml.m5.large     ml.t2.medium
    create-endpoint -> InService (2 variants)  6 min 47 s      7 min 03 s / 7 min 33 s
    update-endpoint to a new config            3 min 47 s      (not re-run)
    weight shift (rollback)                    ~85 s           ~92 s
    observed split at 9:1 over 200 calls       175 / 25        174 / 26 and 182 / 18
    observed split at 10:0 over 100 calls      100 / 0         100 / 0
    ModelLatency steady state                  ~4,150 us       ~3,793-4,628 us
    ModelLatency first call after deploy       ~24,000 us      ~27,696 us (6.0x)
    alarm OK -> ALARM after breach             < 60 s          (not re-run)

The burstable instance is NOT meaningfully slower for this model. t2.medium
costs $0.056/hr against m5.large's $0.115 and serves the same latency, which is
why Lab 5 now starts students there - see the instance/quota section in the lab
spec for the auto-scaling trade that comes with it.

CAPTURE LAYOUT (verified 2026-07-31)
------------------------------------
    datacapture/<endpoint>/<variant>/<yyyy>/<mm>/<dd>/<hh>/<mm-ss-ms>-<uuid>.jsonl

Written ASYNCHRONOUSLY, several minutes behind the invocation. Each record holds
captureData.endpointInput, captureData.endpointOutput and
eventMetadata.inferenceTime. A two-variant canary produces TWO prefixes; Lab 6's
analyzer must be pointed at one of them.

THREE THINGS THAT WILL BITE YOU
-------------------------------
1. ModelLatency is emitted in MICROSECONDS. 200 ms is 200000. A threshold of 200
   means 0.2 ms, which is far below healthy latency, so the alarm sits in ALARM
   forever and any rollback wired to it fires on every deploy.

2. Burstable instances (ml.t2.*, ml.t3.*) cannot be auto-scaling targets. The
   endpoint deploys fine on them and fails only at RegisterScalableTarget.

3. Rolling back to weight 0 stops the canary SERVING. It does not stop the canary
   BILLING. Only delete-endpoint does that. Run scripts/teardown-lab5.sh.

Usage:
    python canary_deploy_realtime.py --model-package-arn <arn> --role-arn <arn>
"""

import argparse
import collections
import json
import logging
import pathlib
import time

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REGION = "us-east-1"
ENDPOINT_NAME = "northstar-churn-prod"

# DEFAULT IS BURSTABLE, AND THAT IS DELIBERATE.
#
# ml.t2.medium is where Lab 5 starts students: it is the cheapest real-time
# instance ($0.056/hr) and one of only three endpoint types with a non-zero
# AWS DEFAULT quota. ml.m5.large defaults to 0 on a new account, so a student
# who starts there cannot deploy at all.
#
# The trade is that auto-scaling REJECTS burstable types. Hitting that wall,
# diagnosing it, and then discovering the m5.large quota is 0 is the designed
# arc of Lab 5 Task 1 — not a mistake to be engineered around.
#
# Override with --instance-type ml.m5.large once a quota increase (L-614B09FD)
# has been approved.
DEFAULT_INSTANCE_TYPE = "ml.t2.medium"

CHAMPION_VARIANT = "champion"
CANARY_VARIANT = "canary"
CANARY_INITIAL_WEIGHT = 1        # 10% of traffic
CHAMPION_INITIAL_WEIGHT = 9      # 90% of traffic

# ModelLatency is in MICROSECONDS. 200 ms == 200000 us.
ROLLBACK_LATENCY_THRESHOLD_US = 200_000
# Two periods, not one: the first invocation after a deploy is a ~24,000 us cold
# start and would trip a single-period alarm against a healthy deployment.
ROLLBACK_EVALUATION_PERIODS = 2

# The lab compresses the production 48-hour canary window to 60 minutes. What the
# short window cannot catch - drift, diurnal load, anything needing label feedback -
# must be stated in the deployment plan. See Task 1.
PRODUCTION_MONITOR_WINDOW_HOURS = 48
LAB_MONITOR_WINDOW_MINUTES = 60


def create_models(sm, model_package_arn: str, role_arn: str) -> None:
    """Create the two SageMaker Models the canary endpoint routes between.

    In a real promotion these point at different Model Registry versions. Pointing
    both at the same package is fine for rehearsing the mechanics.
    """
    for name in (f"northstar-churn-{CHAMPION_VARIANT}", f"northstar-churn-{CANARY_VARIANT}"):
        try:
            sm.create_model(
                ModelName=name,
                PrimaryContainer={"ModelPackageName": model_package_arn},
                ExecutionRoleArn=role_arn,
            )
            logger.info("created model %s", name)
        except sm.exceptions.ClientError as exc:
            if "already exists" not in str(exc):
                raise
            logger.info("model %s already exists", name)


def deploy_canary(sm, config_name: str, data_capture_s3_uri: str,
                  instance_type: str = DEFAULT_INSTANCE_TYPE) -> None:
    """Create a two-variant endpoint at 90/10, or update an existing one.

    update-endpoint is SageMaker's managed blue/green: the old fleet keeps serving
    until the new fleet is healthy. Measured at 3 min 47 s with zero downtime.

    DataCaptureConfig is enabled here because Lab 6 cannot exist without it.
    Lab 6's Evidently drift job analyses captured inference data; if capture is
    off there is nothing to analyse, and a drift check against an empty window
    looks exactly like a healthy one. Endpoint configs are IMMUTABLE, so
    capture cannot be switched on later without creating a new config and calling
    update-endpoint (3 min 47 s). Turning it on at deploy time costs nothing beyond
    S3 storage, which the datacapture/ lifecycle rule expires after 7 days.
    """
    sm.create_endpoint_config(
        EndpointConfigName=config_name,
        DataCaptureConfig={
            "EnableCapture": True,
            # 100% while the lab is running. Production would sample far lower;
            # say so in the deployment plan rather than silently shipping 100%.
            "InitialSamplingPercentage": 100,
            "DestinationS3Uri": data_capture_s3_uri,
            # Both halves are required for data quality monitoring: the baseline
            # is computed from Input, and Output is what makes prediction-drift
            # analysis possible later.
            "CaptureOptions": [
                {"CaptureMode": "Input"},
                {"CaptureMode": "Output"},
            ],
            "CaptureContentTypeHeader": {
                "CsvContentTypes": ["text/csv"],
            },
        },
        ProductionVariants=[
            {
                "VariantName": CHAMPION_VARIANT,
                "ModelName": f"northstar-churn-{CHAMPION_VARIANT}",
                "InitialInstanceCount": 1,
                "InstanceType": instance_type,
                "InitialVariantWeight": CHAMPION_INITIAL_WEIGHT,
            },
            {
                "VariantName": CANARY_VARIANT,
                "ModelName": f"northstar-churn-{CANARY_VARIANT}",
                "InitialInstanceCount": 1,
                "InstanceType": instance_type,
                "InitialVariantWeight": CANARY_INITIAL_WEIGHT,
            },
        ],
    )
    logger.info("created endpoint config %s", config_name)

    existing = [e["EndpointName"] for e in sm.list_endpoints()["Endpoints"]]
    if ENDPOINT_NAME in existing:
        sm.update_endpoint(EndpointName=ENDPOINT_NAME, EndpointConfigName=config_name)
        logger.info("updating existing endpoint (managed blue/green)")
    else:
        sm.create_endpoint(EndpointName=ENDPOINT_NAME, EndpointConfigName=config_name)
        logger.info("creating endpoint - expect about 7 minutes")

    waiter_start = time.time()
    while True:
        status = sm.describe_endpoint(EndpointName=ENDPOINT_NAME)["EndpointStatus"]
        if status == "InService":
            logger.info("endpoint InService after %.0fs", time.time() - waiter_start)
            return
        if status == "Failed":
            reason = sm.describe_endpoint(EndpointName=ENDPOINT_NAME).get("FailureReason")
            raise RuntimeError(f"endpoint failed: {reason}")
        time.sleep(30)


def observe_traffic_split(payload: bytes, n: int = 200) -> dict:
    """Prove traffic actually split. This is the Task 1 evidence artifact.

    A configured weight is a claim; a counted split is evidence. The rubric awards
    12 points for the observed split and 8 for configuration alone.
    """
    rt = boto3.client("sagemaker-runtime", region_name=REGION)
    counts = collections.Counter()
    for _ in range(n):
        resp = rt.invoke_endpoint(
            EndpointName=ENDPOINT_NAME, ContentType="text/csv", Body=payload
        )
        counts[resp["InvokedProductionVariant"]] += 1
    logger.info("observed split over %d invocations: %s", n, dict(counts))
    return dict(counts)


def smoke_test_variant(payload: bytes, variant: str) -> str:
    """Invoke one variant directly, bypassing the weights.

    Required for blue/green validation: you must be able to exercise the new model
    before you send it any real traffic.
    """
    rt = boto3.client("sagemaker-runtime", region_name=REGION)
    resp = rt.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="text/csv",
        Body=payload,
        TargetVariant=variant,
    )
    body = resp["Body"].read().decode().strip()
    logger.info("smoke test %s -> %s", variant, body)
    return body


def configure_rollback_alarm(cw) -> None:
    """CloudWatch alarm on canary p95 latency, in microseconds.

    Task 1 requires the trigger to exist as code, not only as prose in the plan.
    """
    cw.put_metric_alarm(
        AlarmName="northstar-churn-rollback-latency",
        AlarmDescription=(
            "Rollback trigger: churn canary p95 ModelLatency above 200 ms "
            "(200000 microseconds) for 2 consecutive minutes"
        ),
        Namespace="AWS/SageMaker",
        MetricName="ModelLatency",
        Dimensions=[
            {"Name": "EndpointName", "Value": ENDPOINT_NAME},
            {"Name": "VariantName", "Value": CANARY_VARIANT},
        ],
        ExtendedStatistic="p95",
        Period=60,
        EvaluationPeriods=ROLLBACK_EVALUATION_PERIODS,
        Threshold=ROLLBACK_LATENCY_THRESHOLD_US,
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
    )
    logger.info(
        "rollback alarm set at %d us (%d ms), %d evaluation periods",
        ROLLBACK_LATENCY_THRESHOLD_US,
        ROLLBACK_LATENCY_THRESHOLD_US // 1000,
        ROLLBACK_EVALUATION_PERIODS,
    )


def shift_weights(sm, champion: int, canary: int) -> None:
    """Promote (10/0 -> 0/10) or roll back (9/1 -> 10/0). Measured at ~85 seconds.

    The endpoint reports Updating throughout and keeps serving. Verified: zero
    dropped requests across the transition.
    """
    sm.update_endpoint_weights_and_capacities(
        EndpointName=ENDPOINT_NAME,
        DesiredWeightsAndCapacities=[
            {"VariantName": CHAMPION_VARIANT, "DesiredWeight": champion},
            {"VariantName": CANARY_VARIANT, "DesiredWeight": canary},
        ],
    )
    while sm.describe_endpoint(EndpointName=ENDPOINT_NAME)["EndpointStatus"] != "InService":
        time.sleep(15)
    logger.info("weights now champion=%d canary=%d", champion, canary)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-package-arn", required=True,
                    help="Approved package from northstar-churn-models")
    ap.add_argument("--role-arn", required=True)
    # Resolved against this file's directory, not the caller's cwd. The old
    # cwd-relative default meant every student who ran this from the repo root
    # -- which is where Lab 5 tells them to run it -- got FileNotFoundError
    # before a single AWS call. The kit does NOT ship sample.csv: it reads the
    # one you created in Lab 5 (one header-less row, 11 features in
    # FEATURE_COLUMNS order). Lost it? git checkout lab5-submit -- deployment/configs/sample.csv
    ap.add_argument("--sample-csv",
                    default=str(pathlib.Path(__file__).resolve().parent / "sample.csv"),
                    help="One CSV row, no header, features in training order")
    ap.add_argument("--rollback", action="store_true",
                    help="Shift canary to weight 0 instead of promoting")
    ap.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE,
                    help="Endpoint instance type. Default ml.t2.medium (has default "
                         "quota; cannot auto-scale). Use ml.m5.large after an "
                         "approved L-614B09FD quota increase.")
    ap.add_argument("--data-capture-s3-uri", default=None,
                    help="Where the endpoint writes captured inference data. "
                         "Defaults to s3://northstar-dev-data-<account>/datacapture/. "
                         "Lab 6's Evidently drift job reads from here.")
    args = ap.parse_args()

    if args.data_capture_s3_uri:
        capture_uri = args.data_capture_s3_uri
    else:
        account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
        capture_uri = f"s3://northstar-dev-data-{account}/datacapture/"
    logger.info("data capture destination %s", capture_uri)

    sm = boto3.client("sagemaker", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    payload = open(args.sample_csv, "rb").read()

    # The package must be Approved. Nothing in Labs 1-4 moves it off
    # PendingManualApproval - that approval is a graded governance step.
    status = sm.describe_model_package(
        ModelPackageName=args.model_package_arn
    )["ModelApprovalStatus"]
    if status != "Approved":
        raise SystemExit(
            f"model package is {status}, not Approved. Approve it first:\n"
            f"  aws sagemaker update-model-package --model-package-arn "
            f"{args.model_package_arn} --model-approval-status Approved"
        )

    create_models(sm, args.model_package_arn, args.role_arn)
    deploy_canary(sm, f"northstar-churn-canary-{int(time.time())}", capture_uri,
                  args.instance_type)
    configure_rollback_alarm(cw)

    smoke_test_variant(payload, CANARY_VARIANT)
    split = observe_traffic_split(payload, n=200)

    if args.rollback:
        shift_weights(sm, champion=10, canary=0)
        after = observe_traffic_split(payload, n=100)
        assert after.get(CANARY_VARIANT, 0) == 0, "canary still serving after rollback"
        logger.info("ROLLBACK COMPLETE - canary at weight 0")
        logger.warning("the canary instance is STILL BILLING. run scripts/teardown-lab5.sh")
    else:
        logger.info(
            "canary live. production plan calls for a %d-hour window; the lab observes "
            "%d minutes. Document that gap in docs/lab5-deployment-plan.md.",
            PRODUCTION_MONITOR_WINDOW_HOURS, LAB_MONITOR_WINDOW_MINUTES,
        )

    print(json.dumps({"observed_split": split, "endpoint": ENDPOINT_NAME}, indent=2))


if __name__ == "__main__":
    main()
