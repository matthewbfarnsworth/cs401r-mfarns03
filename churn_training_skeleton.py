"""
NorthStar Retail — Churn Prediction Training Script (Skeleton)
CS 401R Lab 3 Starter Kit

This script is the SageMaker XGBoost training entry point for the NorthStar
churn prediction model. It reads features from the SageMaker Feature Store,
trains an XGBoost classifier, tracks the run in a SageMaker MLflow App, and
registers the model in the Model Registry.

Experiment tracking needs two packages that are NOT in the training container:

    pip install mlflow sagemaker-mlflow

`sagemaker-mlflow` is the SigV4 auth plugin for arn:aws:sagemaker:... tracking
URIs. Install `mlflow` alone and you get a connection error that never mentions
credentials. See track_run() for the cost warning about the OTHER MLflow
product -- read it before you create anything.

Usage (local testing):
    python churn_training_skeleton.py \
        --feature-group-name northstar-dev-customer-features \
        --artifacts-bucket northstar-dev-data-<account> \
        --winner-config output/tuning/tuning-winner.json \
        --mlflow-arn arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX \
        --run-name xgb-final

Usage (SageMaker training job — parameters passed via hyperparameter dict):
    Configured via the SageMaker Python SDK Estimator.
"""

import argparse
import json
import os
import pickle
import tarfile
import tempfile
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd

# SageMaker imports — available in SageMaker training containers.
#
# The two failure modes below are different problems and used to produce the
# same message. sagemaker 3.x REMOVED the feature_store package, so on a 3.x
# install the SDK imports fine and only the second line fails. Reporting that
# as "sagemaker not available" sends you looking for a missing install that is
# not missing. Pin sagemaker<3.0.0 (see requirements.txt, defect 55).
try:
    import sagemaker
except ImportError:
    sagemaker = None
    SAGEMAKER_AVAILABLE = False
    print("WARNING: sagemaker not installed — running in local test mode")
else:
    try:
        from sagemaker import image_uris
        from sagemaker.feature_store.feature_group import FeatureGroup
        from sagemaker.session import Session
        SAGEMAKER_AVAILABLE = True
    except ImportError:
        SAGEMAKER_AVAILABLE = False
        print("WARNING: sagemaker is installed but has no feature_store module. "
              "This is sagemaker 3.x, which dropped it. Run "
              "`pip install 'sagemaker>=2.200.0,<3.0.0'` — the Feature Store "
              "path in this script cannot work on 3.x.")

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    raise ImportError("xgboost is required: pip install xgboost")

from sklearn.metrics import (auc, classification_report, precision_recall_curve,
                              roc_auc_score)
from sklearn.model_selection import train_test_split

# ── Argument Parsing ───────────────────────────────────────────────────────────
# SageMaker passes hyperparameters as CLI arguments.

def parse_args():
    parser = argparse.ArgumentParser(description="NorthStar churn prediction training")

    # Data configuration
    # Names come from Lab 2's Terraform: `terraform output -raw feature_group_name`
    # and `terraform output -raw s3_bucket_name`. Do not invent new ones.
    parser.add_argument("--feature-group-name", type=str,
                        default="northstar-dev-customer-features",
                        help="SageMaker Feature Store feature group name (Lab 2 output)")
    # These dates bound the INGEST batch, not the feature window. Lab 2 stamps
    # event_time with the wall-clock time its Glue job ran (time.time()), not
    # with the feature cutoff T = 2026-04-01 -- T was already enforced when the
    # features were computed. A window that ends before your Lab 2 run (the old
    # default here was 2026-06-01) matches zero rows. These defaults match the
    # reference implementation and cover any Lab 2 run during the semester.
    parser.add_argument("--training-start-date", type=str, default="2026-01-01",
                        help="Earliest Lab 2 ingest date to include (YYYY-MM-DD)")
    parser.add_argument("--training-end-date", type=str, default="2027-01-01",
                        help="Latest Lab 2 ingest date to include (YYYY-MM-DD)")
    parser.add_argument("--artifacts-bucket", type=str, required=True,
                        help="Your Lab 1 data bucket, northstar-dev-data-<account>. "
                             "Athena results and model artifacts go under its artifacts/ "
                             "prefix, the only prefix your MLEngineer role can write")
    parser.add_argument("--local-data-path", type=str, default=None,
                        help="Path to local feature CSV for development testing (bypasses Feature Store)")

    # XGBoost hyperparameters
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--eta", type=float, default=0.1)
    parser.add_argument("--num-round", type=int, default=200)
    parser.add_argument("--min-child-weight", type=int, default=5)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--scale-pos-weight", type=float, default=None,
                        help=(
                            "XGBoost positive-class weight. Leave unset and it is computed "
                            "from YOUR training split as negatives/positives, which is what "
                            "you want. The reference 10,000-customer dataset is 22.0%% "
                            "churners, giving (1 - 0.220) / 0.220 = 3.545. Passing a value "
                            "tuned for a different class balance will quietly skew your "
                            "precision/recall trade-off, so only override this deliberately."
                        ))
    # Experiment tracking (Task 1, 5 points)
    parser.add_argument("--mlflow-arn", type=str,
                        default=os.environ.get("MLFLOW_APP_ARN"),
                        help=(
                            "ARN of your SageMaker MLflow APP, e.g. "
                            "arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX. "
                            "Create it once with `aws sagemaker create-mlflow-app`. "
                            "This is NOT an MLflow Tracking Server -- see the warning "
                            "on track_run() below. Omit to skip tracking."
                        ))
    parser.add_argument("--mlflow-experiment", type=str, default="northstar-churn",
                        help="MLflow experiment name; runs group under this")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Name for this run. Make it descriptive -- 'run3' tells "
                             "you nothing three weeks later.")

    parser.add_argument(
        "--winner-config",
        type=str,
        required=True,
        help=(
            "Path to tuning-winner.json produced by churn_tuning.py. The final "
            "model is retrained on the full 70%% development split using this "
            "configuration and its best number of boosting rounds."
        ),
    )
    parser.add_argument(
        "--model-package-group",
        default="northstar-churn-models",
        help="SageMaker Model Registry package group required by later labs",
    )
    parser.add_argument(
        "--skip-registration",
        action="store_true",
        help="Development only: save local artifacts without uploading/registering",
    )

    parser.add_argument("--model-dir", type=str,
                        default=os.environ.get("SM_MODEL_DIR", "./model"))
    parser.add_argument("--output-data-dir", type=str,
                        default=os.environ.get("SM_OUTPUT_DATA_DIR", "./output"))

    return parser.parse_args()


# ── Feature Store Loading ──────────────────────────────────────────────────────

# These are exactly the features your Lab 2 pipeline wrote to the Feature
# Group. If a name here does not match your feature group, the Athena query
# below will fail - check `aws sagemaker describe-feature-group` first.
#
# Note what is NOT in this list: churn_label (the target) and customer_id
# (an identifier, not a signal). Including either as an input is leakage.
FEATURE_COLUMNS = [
    # Recency and tenure
    "days_since_last_purchase",
    "customer_tenure_days",
    # Frequency
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    # Monetary
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    # Behavioural - these are the features that catch churners who still
    # look active on recency alone. Drop them and your model collapses
    # toward the recency-only baseline.
    "category_diversity_score",
    "online_to_store_ratio",
]

# churn_risk_score is available in the feature group but deliberately excluded
# here: it is a pure recency heuristic and doubles as the baseline you must
# beat. If you add it, report your metrics both with and without it.
BASELINE_COLUMN = "churn_risk_score"

# The recency-only baseline model uses just this one feature. Task 1 requires
# you to train it and show your full model beats it by a margin whose 95%
# confidence interval excludes zero. There is no fixed AUC-lift threshold: a
# ">= 0.03 lift" gate lived here until 2026-08-02 and was removed because 0.03
# is smaller than the metric's own run-to-run standard deviation, so it failed
# on 21% of splits regardless of model quality. See EVAL_THRESHOLDS below.
BASELINE_FEATURE = "days_since_last_purchase"

LABEL_COLUMN = "churn_label"

# Not a feature. This is the column Task 1's slice evaluation groups by, so it
# has to come back from the query even though it never enters the model. Keep it
# out of FEATURE_COLUMNS: loyalty_tier correlates with spend, and feeding it in
# both leaks and makes the per-tier fairness check circular.
SLICE_COLUMN = "loyalty_tier"


def load_features_from_feature_store(feature_group_name: str,
                                     start_date: str,
                                     end_date: str,
                                     artifacts_bucket: str) -> pd.DataFrame:
    """
    Load features from the SageMaker Feature Store offline store using Athena.

    Args:
        feature_group_name: your Lab 2 feature group
        start_date, end_date: ISO dates (YYYY-MM-DD) bounding the event_time range
        artifacts_bucket: S3 bucket that Athena writes its result set to. Athena
            will not run a query without an output location, so this has to be
            threaded in from --artifacts-bucket rather than assumed.

    The query is executed through the Feature Store Athena helper so the
    resulting QueryExecutionId can be preserved as grading evidence.
    Reference: https://docs.aws.amazon.com/sagemaker/latest/dg/feature-store-use-with-studio.html
    """
    if not SAGEMAKER_AVAILABLE:
        raise RuntimeError("SageMaker SDK not available - use --local-data-path for local testing")

    # event_time was written as Fractional (epoch seconds) by the Lab 2 job, so
    # the date bounds must be converted before comparison. Passing ISO strings
    # straight into the query fails with TYPE_MISMATCH rather than returning
    # rows, so at least the failure is loud.
    start_epoch = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    end_epoch = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    session = Session()
    feature_group = FeatureGroup(name=feature_group_name, sagemaker_session=session)

    # Two things in the query below are easy to get wrong, so read them:
    #
    # 1. event_time is Fractional - Unix epoch SECONDS, not an ISO string.
    #    Comparing it to '2026-04-01' fails the query outright with:
    #      TYPE_MISMATCH: Cannot check if double is BETWEEN varchar(10)...
    #    Convert your date bounds to epoch seconds before substituting them.
    #
    # 2. The offline store is append-only. Re-running the Lab 2 feature job
    #    writes a SECOND record for every customer, and a naive SELECT then
    #    returns duplicate customers with stale feature values. Deduplicate
    #    with a window function keyed on customer_id, keeping the most recent
    #    event_time. Filtering on a global MAX(write_time) does NOT work -
    #    it keeps only the customers written in the final microsecond.
    #
    #    The offline store also soft-deletes: rows carry is_deleted, and
    #    deleted records must be excluded.
    #
    # 3. The outer SELECT must ORDER BY customer_id, and this is the one people
    #    skip because it looks cosmetic. It is not. Athena parallelises the scan
    #    across the offline store's Parquet objects and returns rows in whatever
    #    order the splits happen to finish, which varies from run to run.
    #    train_test_split(random_state=42) is deterministic only for a GIVEN row
    #    order -- so without ORDER BY, the identical data produces a different
    #    train/test split, and therefore different metrics, on every single run.
    #
    #    This was measured, not theorised: four runs on byte-identical data
    #    produced AUC between 0.7276 and 0.7431, and a Platinum-slice AUC
    #    between 0.430 and 0.700. An entire "the model is worse than random on
    #    your best customers" finding turned out to be an artefact of row order.
    #
    #    The rn = 1 filter guarantees customer_id is unique here, so ordering on
    #    it is a TOTAL order and the pipeline becomes reproducible.
    #
    #    If your metrics move between runs and your data did not, this is why.

    #    loyalty_tier is selected but is NOT in FEATURE_COLUMNS, and that is
    #    deliberate. It is not an input to the model - it is the column Task 1's
    #    slice evaluation groups by. Keep it out of the feature matrix (see
    #    preprocess_features, which selects FEATURE_COLUMNS explicitly) and
    #    carry it alongside X and y through the split so each row's prediction
    #    can still be attributed to a tier:
    #
    #        X_train, X_val, y_train, y_val, tier_train, tier_val = \
    #            train_test_split(X, y, df[SLICE_COLUMN], test_size=0.30,
    #                             random_state=42, stratify=y)
    #
    #    Split X and y without it and you cannot line the tiers back up.

    query = feature_group.athena_query()
    query_string = f"""
        WITH ranked AS (
            SELECT
                customer_id,
                {", ".join(FEATURE_COLUMNS)},
                {LABEL_COLUMN},
                {SLICE_COLUMN},
                event_time,
                ROW_NUMBER() OVER (
                    PARTITION BY customer_id
                    ORDER BY event_time DESC, write_time DESC
                ) AS rn
            FROM "{query.table_name}"
            WHERE event_time BETWEEN {start_epoch} AND {end_epoch}
              AND NOT is_deleted
        )
        SELECT
            customer_id,
            {", ".join(FEATURE_COLUMNS)},
            {LABEL_COLUMN},
            {SLICE_COLUMN}
        FROM ranked
        WHERE rn = 1
        ORDER BY customer_id
    """

    query.run(
        query_string=query_string,
        output_location=f"s3://{artifacts_bucket}/artifacts/athena-results/",
    )
    query_execution = query.get_query_execution()["QueryExecution"]
    print(f"Athena QueryExecutionId: {query_execution['QueryExecutionId']}")
    query.wait()
    return query.as_dataframe()


def load_features_local(path: str) -> pd.DataFrame:
    """Load features from a local CSV file (development mode)."""
    print(f"Loading features from local file: {path}")
    df = pd.read_csv(path)
    missing = [c for c in FEATURE_COLUMNS + [LABEL_COLUMN] if c not in df.columns]
    if missing:
        raise ValueError(f"Local data missing required columns: {missing}")
    # Not fatal - the model trains fine without it - but Task 1's slice
    # evaluation cannot be done at all, so fail the task rather than the run.
    if SLICE_COLUMN not in df.columns:
        print(f"WARNING: '{SLICE_COLUMN}' not in local data — Task 1 slice "
              f"evaluation will not be possible with this file")
    return df


# ── Feature Preprocessing ──────────────────────────────────────────────────────

def preprocess_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    Clean and prepare features for XGBoost training.
    Returns (X, y) arrays.
    """
    print(f"Preprocessing {len(df):,} samples...")

    # Drop rows where label is missing
    df = df.dropna(subset=[LABEL_COLUMN])

    # Sentinel -1 preserves the distinction between a missing behavioral value
    # and a measured zero without learning imputation statistics from the test set.
    X = df[FEATURE_COLUMNS].fillna(-1).values
    y = df[LABEL_COLUMN].values.astype(int)

    print(f"  Positive class rate: {y.mean():.1%}")
    print(f"  Feature matrix shape: {X.shape}")

    return X, y


# ── Model Training ─────────────────────────────────────────────────────────────

def build_xgb_params(args, y_train: np.ndarray) -> dict:
    """Build XGBoost parameters and record the resolved class weight."""
    # Class imbalance, computed from the training split rather than hardcoded.
    # On the reference dataset this lands at 3.545.
    spw = (args.scale_pos_weight if args.scale_pos_weight is not None
           else float((y_train == 0).sum() / max((y_train == 1).sum(), 1)))

    # Record the RESOLVED value so track_run() can log what was actually used.
    # Logging args.scale_pos_weight instead would log None on every run that
    # let it compute itself -- which is most of them -- and your MLflow
    # comparison would be missing the one parameter most likely to explain a
    # precision/recall difference between runs.
    args.resolved_scale_pos_weight = spw

    return {
        # eval_metric ORDER MATTERS. XGBoost early-stops on the LAST metric in
        # this list, not the first. This used to read ["auc", "logloss"], which
        # silently made logloss the early-stopping criterion: under
        # scale_pos_weight the reweighted logloss keeps improving long after
        # ranking quality peaks, so training ran the full 200 rounds instead of
        # stopping near round 31 and validation AUC came out 0.7603 instead of
        # 0.7822. Measured across 50 splits, that cost the model the promotion
        # gate on 28% of them -- a student doing everything right was told their
        # features did not beat recency. Keep "auc" last (defect 49).
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "max_depth": args.max_depth,
        "eta": args.eta,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "scale_pos_weight": spw,
        "seed": 42,
        "verbosity": 1,
    }


def train_model(X_train: np.ndarray,
                y_train: np.ndarray,
                args,
                X_val: np.ndarray = None,
                y_val: np.ndarray = None) -> xgb.Booster:
    """Train XGBoost, using validation and early stopping only when provided."""
    if (X_val is None) != (y_val is None):
        raise ValueError("X_val and y_val must either both be provided or both be omitted")

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    params = build_xgb_params(args, y_train)

    print(f"\nTraining XGBoost with params: {json.dumps(params, indent=2)}")

    evals = [(dtrain, "train")]
    training_options = {}
    if X_val is not None:
        dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_COLUMNS)
        evals.append((dval, "validation"))
        training_options["early_stopping_rounds"] = 20

    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_round,
        evals=evals,
        verbose_eval=25,
        **training_options,
    )

    return model


def predict_model(model: xgb.Booster, data: xgb.DMatrix) -> np.ndarray:
    """Predict with the early-stopping winner, or all rounds after final retraining."""
    if hasattr(model, "best_iteration"):
        return model.predict(
            data,
            iteration_range=(0, model.best_iteration + 1),
        )
    return model.predict(data)


def top10_selection(y_pred_proba: np.ndarray) -> tuple[float, np.ndarray]:
    """Return the agreed global 90th-percentile cutoff and inclusive mask."""
    threshold = float(np.percentile(y_pred_proba, 90))
    return threshold, y_pred_proba >= threshold


# ── Evaluation ─────────────────────────────────────────────────────────────────

EVAL_THRESHOLDS = {
    "precision_top10":   0.50,   # Minimum precision @ top 10% scored customers
    "recall_top10":      0.25,   # Minimum recall @ top 10% scored customers
    # Note the recall ceiling: with ~22% positives, targeting the top 10% of
    # customers caps achievable recall near 0.45. Hitting 0.25 means capturing
    # roughly half of what is reachable within that contact budget.
}

# There is deliberately NO absolute AUC threshold. One used to live here
# (auc_roc >= 0.72) and it was removed on 2026-08-02: measured across 200
# random train/test splits of the same data, the reference model fell below
# 0.72 on 58% of them. A gate the reference clears by luck of the shuffle
# grades your random seed, not your model. Report AUC; do not gate on it.
#
# The baseline gate is an INTERVAL, not a threshold: the 95% CI on
# (your AUC - recency-only baseline AUC) must exclude zero.
# BASELINE_FEATURE is defined once, near FEATURE_COLUMNS above.
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42


def train_recency_baseline(X_train: np.ndarray,
                           y_train: np.ndarray,
                           X_val: np.ndarray) -> np.ndarray:
    """Train the recency-only baseline and score the validation split.

    The baseline is a model over `days_since_last_purchase` ALONE, trained on
    the same training rows and scored on the same validation rows as the full
    model. That is what makes the comparison fair.

    It is deliberately NOT a constant predictor. A constant prediction has an
    AUC of exactly 0.5 by construction, so "beating" it proves only that your
    model is better than a coin flip. The question Lab 3 asks is whether your
    feature engineering beats the rule the business already has for free:
    "contact whoever has not purchased in a while."
    """
    i = FEATURE_COLUMNS.index(BASELINE_FEATURE)
    dtr = xgb.DMatrix(X_train[:, [i]], label=y_train, feature_names=[BASELINE_FEATURE])
    dva = xgb.DMatrix(X_val[:, [i]], feature_names=[BASELINE_FEATURE])
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    params = {
        "objective": "binary:logistic", "eval_metric": "auc",
        "max_depth": 4, "eta": 0.1, "subsample": 0.9,
        "colsample_bytree": 0.9, "scale_pos_weight": spw, "seed": 42,
    }
    return xgb.train(params, dtr, num_boost_round=200).predict(dva)


def bootstrap_lift_ci(y_true: np.ndarray,
                      proba: np.ndarray,
                      baseline_proba: np.ndarray,
                      n: int = BOOTSTRAP_N,
                      seed: int = BOOTSTRAP_SEED,
                      alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI for (model AUC - baseline AUC), by resampling the val set.

    PAIRED: each replicate resamples row indices once and scores BOTH models on
    those same rows, so the interval is on the difference and the correlation
    between the two models is preserved. Resampling them independently breaks
    the pairing and inflates the interval, which would make a real improvement
    look inconclusive.

    Replicates whose resample happens to be single-class are skipped, because
    AUC is undefined there.

    The seed is fixed so the gate is reproducible: the same split must always
    produce the same verdict.
    """
    rng = np.random.default_rng(seed)
    y_true = np.asarray(y_true)
    idx = np.arange(len(y_true))
    diffs = []
    for _ in range(n):
        s = rng.choice(idx, size=len(idx), replace=True)
        ys = y_true[s]
        if len(np.unique(ys)) < 2:
            continue
        diffs.append(roc_auc_score(ys, proba[s]) - roc_auc_score(ys, baseline_proba[s]))
    if len(diffs) < n // 2:
        raise ValueError(
            "Too few usable bootstrap replicates to form a confidence interval. "
            "Your validation set is too small or too imbalanced to support this gate."
        )
    d = np.sort(diffs)
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))


def evaluate_model(model: xgb.Booster,
                   X_val: np.ndarray,
                   y_val: np.ndarray,
                   baseline_proba: np.ndarray,
                   enforce_thresholds: bool = True) -> dict:
    """
    Evaluate the trained model against required thresholds.

    Metrics are returned at full precision; round only when displaying them.
    Tuning calls this with enforce_thresholds=False so every attempted
    configuration can still be logged to MLflow. Final evaluation keeps the
    default True so a failed gate blocks registration.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = predict_model(model, dval)

    auc_roc = roc_auc_score(y_val, y_pred_proba)

    # Precision and recall @ top 10%
    top10_threshold, top10_mask = top10_selection(y_pred_proba)
    top10_true_positives = int(y_val[top10_mask].sum())
    precision_top10 = (
        top10_true_positives / top10_mask.sum()
        if top10_mask.sum() > 0
        else 0.0
    )
    recall_top10 = (
        top10_true_positives / y_val.sum() if y_val.sum() > 0 else 0.0
    )

    # Baseline: a model trained on days_since_last_purchase ALONE.
    #
    # This used to be `np.full_like(y_pred_proba, y_val.mean())` - a constant
    # prediction, whose AUC is exactly 0.5 by definition. Beating a coin flip
    # is not evidence that your feature engineering did anything, and it is not
    # what Lab 3 asks for. The comparison that matters is against recency,
    # because recency is the rule the business already has for free.
    #
    baseline_auc = roc_auc_score(y_val, baseline_proba)
    auc_vs_baseline = auc_roc - baseline_auc

    lift_ci_low, lift_ci_high = bootstrap_lift_ci(
        y_val, y_pred_proba, baseline_proba)

    metrics = {
        "auc_roc": float(auc_roc),
        "precision_top10": float(precision_top10),
        "recall_top10": float(recall_top10),
        "auc_vs_baseline": float(auc_vs_baseline),
        "baseline_auc": float(baseline_auc),
        "lift_ci_low": float(lift_ci_low),
        "lift_ci_high": float(lift_ci_high),
        "positive_rate_val": float(y_val.mean()),
        "n_val_samples": int(len(y_val)),
        "top10_probability_threshold": top10_threshold,
        "top10_selected_count": int(top10_mask.sum()),
        "top10_true_positives": top10_true_positives,
        "top10_selected_rate": float(top10_mask.mean()),
        "eval_timestamp": datetime.utcnow().isoformat(),
    }

    print("\n── Evaluation Results ──────────────────────────────")
    failures = []
    for metric, threshold in EVAL_THRESHOLDS.items():
        value = metrics[metric]
        status = "✓ PASS" if value >= threshold else "✗ FAIL"
        print(f"  {metric:25s}: {value:.4f}  (threshold: {threshold:.4f})  {status}")
        if value < threshold:
            failures.append(f"{metric}={value:.4f} < {threshold}")

    # The baseline gate: the interval must exclude zero.
    ci_status = "\u2713 PASS" if lift_ci_low > 0 else "\u2717 FAIL"
    print(f"  {'auc_lift 95% CI':25s}: [{lift_ci_low:.4f}, {lift_ci_high:.4f}]"
          f"  (must exclude 0)  {ci_status}")
    if lift_ci_low <= 0:
        failures.append(
            f"auc_lift 95% CI [{lift_ci_low:.4f}, {lift_ci_high:.4f}] includes "
            f"zero - no evidence the model beats the recency-only baseline")

    metrics["passed_gates"] = int(not failures)
    metrics["gate_failure_count"] = len(failures)

    if failures:
        failure_message = (
            f"Model failed evaluation thresholds: {'; '.join(failures)}. "
            f"Do not promote to Model Registry."
        )
        if enforce_thresholds:
            raise ValueError(failure_message)
        print(f"  Gates failed; recording this tuning run without promotion.")
        return metrics

    print("  All thresholds passed ✓")

    return metrics


# ── Slice Evaluation ───────────────────────────────────────────────────────────

def evaluate_slices(model: xgb.Booster,
                    X_val: np.ndarray,
                    y_val: np.ndarray,
                    slice_column: pd.Series) -> list[dict]:
    """
    Evaluate AUC and recall for each loyalty tier on the final test set.

    Recall uses the same global top-10% contact list as the aggregate rubric
    metric. A tier is too small for a conclusion when it contains fewer than
    20 positives OR fewer than 20 negatives, per the chosen rule.
    """
    tiers = pd.Series(slice_column).reset_index(drop=True)
    if len(tiers) != len(y_val):
        raise ValueError("Slice labels must align one-for-one with evaluation rows")

    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = predict_model(model, dval)
    top10_threshold, top10_mask = top10_selection(y_pred_proba)
    aggregate_recall = (
        float(y_val[top10_mask].sum() / y_val.sum()) if y_val.sum() else 0.0
    )

    slice_results = []
    for slice_value in sorted(tiers.dropna().unique()):
        slice_mask = (tiers == slice_value).to_numpy()
        slice_y = y_val[slice_mask]
        slice_proba = y_pred_proba[slice_mask]
        positives = int(slice_y.sum())
        negatives = int(len(slice_y) - positives)
        selected_positives = int(y_val[slice_mask & top10_mask].sum())
        recall = float(selected_positives / positives) if positives else 0.0
        slice_auc = (
            float(roc_auc_score(slice_y, slice_proba))
            if positives and negatives
            else None
        )
        slice_results.append({
            "tier": str(slice_value),
            "n": int(len(slice_y)),
            "positives": positives,
            "negatives": negatives,
            "auc": slice_auc,
            "recall": recall,
            "aggregate_recall": aggregate_recall,
            "recall_gap_vs_aggregate": recall - aggregate_recall,
            "recall_drop_over_10pp": int(recall < aggregate_recall - 0.10),
            "too_small_to_conclude": int(positives < 20 or negatives < 20),
            "global_top10_threshold": top10_threshold,
        })

    if slice_results:
        weakest_recall = min(row["recall"] for row in slice_results)
        for row in slice_results:
            row["weakest_tier_by_recall"] = int(
                np.isclose(row["recall"], weakest_recall)
            )

    return slice_results


def save_evaluation_artifacts(model: xgb.Booster,
                              X_test: np.ndarray,
                              y_test: np.ndarray,
                              test_tiers: pd.Series,
                              output_dir: str) -> list[dict]:
    """Write the three fixed-name Task 1 evaluation artifacts."""
    os.makedirs(output_dir, exist_ok=True)
    mpl_cache = os.path.join(tempfile.gettempdir(), "northstar-matplotlib")
    os.makedirs(mpl_cache, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", mpl_cache)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import ConfusionMatrixDisplay, confusion_matrix

    dtest = xgb.DMatrix(X_test, feature_names=FEATURE_COLUMNS)
    y_pred_proba = predict_model(model, dtest)
    threshold, selected = top10_selection(y_pred_proba)
    y_pred = selected.astype(int)

    matrix = confusion_matrix(y_test, y_pred, labels=[0, 1])
    display = ConfusionMatrixDisplay(
        confusion_matrix=matrix,
        display_labels=["Not churn", "Churn"],
    )
    display.plot(cmap="Blues", colorbar=False)
    display.ax_.set_title(
        f"Final Test Confusion Matrix\nGlobal top-10% cutoff = {threshold:.4f}"
    )
    plt.tight_layout()
    confusion_path = os.path.join(output_dir, "confusion-matrix.png")
    plt.savefig(confusion_path, dpi=160, bbox_inches="tight")
    plt.close()

    gains = model.get_score(importance_type="gain")
    importance = pd.DataFrame({
        "feature": FEATURE_COLUMNS,
        "gain": [float(gains.get(feature, 0.0)) for feature in FEATURE_COLUMNS],
    }).sort_values("gain", ascending=True)
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.barh(importance["feature"], importance["gain"], color="#2f6f9f")
    ax.set_xlabel("Gain")
    ax.set_title("XGBoost Feature Importance — Final Registered Model")
    fig.tight_layout()
    importance_path = os.path.join(output_dir, "feature-importance.png")
    fig.savefig(importance_path, dpi=160, bbox_inches="tight")
    plt.close(fig)

    slice_results = evaluate_slices(model, X_test, y_test, test_tiers)
    slices_path = os.path.join(output_dir, "slice-evaluation.csv")
    pd.DataFrame(slice_results).to_csv(slices_path, index=False)

    print(f"✓ Confusion matrix: {confusion_path}")
    print(f"✓ Feature importance: {importance_path}")
    print(f"✓ Slice evaluation: {slices_path}")
    return slice_results


# ── Experiment Tracking (MLflow App) ───────────────────────────────────────────

def track_run(args,
              metrics: dict,
              scale_pos_weight: float,
              tags: dict | None = None) -> str | None:
    """
    Log this training run to your SageMaker MLflow App.

    Worth 5 points in Task 1: >=3 runs, each with logged params AND metrics,
    retrievable via mlflow.search_runs.

    ###########################################################################
    #  THERE ARE TWO MLflow PRODUCTS ON SAGEMAKER. USE THE APP.               #
    #                                                                         #
    #    CreateMlflowApp            serverless   NO ADDITIONAL CHARGE   <-- ok #
    #    CreateMlflowTrackingServer $0.60/hour   until you delete it    <-- NO #
    #                                                                         #
    #  $0.60/hr breaches the entire $10 course budget in 16.7 hours and costs #
    #  about $43 over a weekend -- more per hour than any endpoint in this    #
    #  course. It is not an endpoint, so "did I delete my endpoints?" will    #
    #  not find it. Most tutorials describe the Tracking Server because it    #
    #  shipped first. If anything asks you to choose a size (Small/Medium),   #
    #  you are on the wrong product.                                          #
    ###########################################################################

    Two things that fail in ways the error does not explain:

      1. `create-mlflow-app` postdates many installed AWS CLIs. An old CLI
         says "Invalid choice: 'create-mlflow-app'", which reads like a typo
         rather than a version problem. Check `aws --version` and upgrade.

      2. You need BOTH `mlflow` and `sagemaker-mlflow` installed. The second
         is the SigV4 auth plugin for arn:aws:sagemaker:... tracking URIs.
         Without it you get a connection error that never mentions credentials.

    Returns the MLflow run ID, or None when tracking was explicitly omitted.
    """
    if not args.mlflow_arn:
        print("\n(no --mlflow-arn given; skipping experiment tracking)")
        return

    import mlflow

    mlflow.set_tracking_uri(args.mlflow_arn)   # the App ARN, verbatim
    mlflow.set_experiment(args.mlflow_experiment)

    with mlflow.start_run(run_name=args.run_name) as run:
        mlflow.log_params({
            "max_depth": args.max_depth,
            "eta": args.eta,
            "num_round": args.num_round,
            "min_child_weight": args.min_child_weight,
            "subsample": args.subsample,
            "colsample_bytree": args.colsample_bytree,
            "scale_pos_weight": scale_pos_weight,
            "xgboost_version": xgb.__version__,
        })
        mlflow.log_metrics({
            k: float(v)
            for k, v in metrics.items()
            if isinstance(v, (int, float, np.integer, np.floating))
        })
        if tags:
            mlflow.set_tags(tags)
        run_id = run.info.run_id

    # LOG THE XGBOOST VERSION AS A PARAM. Lab 3 documents that identical data
    # and an identical split produce different metrics on XGBoost 3.2.0 vs 1.7
    # (the SageMaker training container is 1.7). If you train some runs locally
    # and some through SageMaker and do not record the version, you will end up
    # comparing two things that were never comparable and cannot tell why.
    #
    # THREE RUNS IS THE FLOOR, NOT THE GOAL. Three runs with identical
    # hyperparameters and different seeds is the same experiment three times;
    # it earns 2 of 5. Vary something you can defend, and be able to say what
    # the variation did to the metrics.

    print(f"✓ MLflow run logged: {run_id}")
    return run_id


# ── Model Saving & Registry ────────────────────────────────────────────────────

def save_and_register_model(model: xgb.Booster,
                             metrics: dict,
                             args) -> str | None:
    """
    Save the model artifact and register it in SageMaker Model Registry.
    Status is set to PendingManualApproval — human approval required before deployment.
    """
    os.makedirs(args.model_dir, exist_ok=True)

    # Save model
    model_path = os.path.join(args.model_dir, "model.xgb")
    model.save_model(model_path)
    print(f"\n✓ Model saved to {model_path}")

    # Save feature metadata alongside model
    metadata = {
        "feature_columns": FEATURE_COLUMNS,
        "label_column": LABEL_COLUMN,
        "hyperparameters": {
            "max_depth": args.max_depth,
            "eta": args.eta,
            "num_round": args.num_round,
        },
        "evaluation_metrics": metrics,
        "training_date": datetime.utcnow().isoformat(),
    }
    with open(os.path.join(args.model_dir, "model_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    archive_path = os.path.join(args.model_dir, "model.tar.gz")
    with tarfile.open(archive_path, "w:gz") as archive:
        # SageMaker's built-in XGBoost inference container expects this name.
        archive.add(model_path, arcname="xgboost-model")
        archive.add(
            os.path.join(args.model_dir, "model_metadata.json"),
            arcname="model_metadata.json",
        )
    print(f"✓ Model archive saved to {archive_path}")

    if args.skip_registration:
        print("(development mode: skipping S3 upload and Model Registry)")
        return None
    if not args.artifacts_bucket:
        raise ValueError("--artifacts-bucket is required for Model Registry upload")
    if not SAGEMAKER_AVAILABLE:
        raise RuntimeError("SageMaker SDK is required to resolve the inference image")

    aws_session = boto3.Session()
    region = aws_session.region_name or "us-east-1"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_key = f"artifacts/lab3/models/{timestamp}/model.tar.gz"
    aws_session.client("s3").upload_file(
        archive_path,
        args.artifacts_bucket,
        model_key,
    )
    model_data_url = f"s3://{args.artifacts_bucket}/{model_key}"
    print(f"✓ Model archive uploaded to {model_data_url}")

    sm_client = aws_session.client("sagemaker")
    groups = sm_client.list_model_package_groups(
        NameContains=args.model_package_group,
        MaxResults=100,
    )["ModelPackageGroupSummaryList"]
    if not any(
        group["ModelPackageGroupName"] == args.model_package_group
        for group in groups
    ):
        sm_client.create_model_package_group(
            ModelPackageGroupName=args.model_package_group,
            ModelPackageGroupDescription="NorthStar churn models",
        )
        print(f"✓ Created model package group: {args.model_package_group}")

    inference_image = image_uris.retrieve(
        framework="xgboost",
        region=region,
        version="1.7-1",
        py_version="py3",
        instance_type="ml.m5.large",
        image_scope="inference",
    )
    registry_metric_keys = (
        "auc_roc",
        "precision_top10",
        "recall_top10",
        "baseline_auc",
        "auc_vs_baseline",
        "lift_ci_low",
        "lift_ci_high",
    )
    customer_metadata = {
        key: str(metrics[key])
        for key in registry_metric_keys
        if key in metrics
    }
    customer_metadata["xgboost_version"] = xgb.__version__
    if metrics.get("mlflow_run_id"):
        customer_metadata["mlflow_run_id"] = str(metrics["mlflow_run_id"])

    response = sm_client.create_model_package(
        ModelPackageGroupName=args.model_package_group,
        ModelPackageDescription=(
            f"NorthStar churn model; final test AUC={metrics['auc_roc']:.4f}; "
            f"trained {datetime.now(timezone.utc).date()}"
        ),
        InferenceSpecification={
            "Containers": [{
                "Image": inference_image,
                "ModelDataUrl": model_data_url,
            }],
            "SupportedContentTypes": ["text/csv"],
            "SupportedResponseMIMETypes": ["text/csv"],
        },
        ModelApprovalStatus="PendingManualApproval",
        CustomerMetadataProperties=customer_metadata,
    )
    package_arn = response["ModelPackageArn"]
    print(f"✓ Registered PendingManualApproval model: {package_arn}")
    return package_arn


# ── Main ───────────────────────────────────────────────────────────────────────

def apply_winner_config(args) -> dict:
    """Load and apply the unique validation winner produced by the tuner."""
    with open(args.winner_config) as handle:
        winner = json.load(handle)

    required = (
        "max_depth",
        "min_child_weight",
        "subsample",
        "colsample_bytree",
        "eta",
        "effective_num_round",
        "passed_gates",
    )
    missing = [key for key in required if key not in winner]
    if missing:
        raise ValueError(f"Winner configuration is missing fields: {missing}")
    if int(winner["passed_gates"]) != 1:
        raise ValueError("Refusing to retrain a tuning configuration that failed validation gates")

    args.max_depth = int(winner["max_depth"])
    args.min_child_weight = int(winner["min_child_weight"])
    args.subsample = float(winner["subsample"])
    args.colsample_bytree = float(winner["colsample_bytree"])
    args.eta = float(winner["eta"])
    args.num_round = int(winner["effective_num_round"])
    if args.num_round < 1:
        raise ValueError("Winner effective_num_round must be at least 1")
    if not args.run_name:
        args.run_name = f"final-from-{winner.get('run_name', 'tuning-winner')}"

    print(f"Loaded validation winner: {args.winner_config}")
    print(
        "Final retraining will use "
        f"depth={args.max_depth}, child_weight={args.min_child_weight}, "
        f"subsample={args.subsample}, colsample={args.colsample_bytree}, "
        f"eta={args.eta}, rounds={args.num_round}"
    )
    return winner


def main():
    args = parse_args()
    os.makedirs(args.model_dir, exist_ok=True)
    os.makedirs(args.output_data_dir, exist_ok=True)

    winner = apply_winner_config(args)
    if not args.skip_registration and args.local_data_path:
        raise ValueError(
            "The registered graded run must use Feature Store/Athena, not --local-data-path. "
            "Use --skip-registration only for development."
        )
    if not args.skip_registration and not args.mlflow_arn:
        raise ValueError(
            "The graded run must be tracked in the MLflow App before registration. "
            "Supply --mlflow-arn or set MLFLOW_APP_ARN."
        )

    print("=" * 60)
    print("NorthStar Churn Prediction — Training Script")
    print("=" * 60)

    # 1. Load features
    if args.local_data_path:
        df = load_features_local(args.local_data_path)
        data_source = "local-development"
    else:
        df = load_features_from_feature_store(
            args.feature_group_name,
            args.training_start_date,
            args.training_end_date,
            args.artifacts_bucket,
        )
        data_source = "feature-store-athena"

    if SLICE_COLUMN not in df.columns:
        raise ValueError(
            f"Final evaluation requires '{SLICE_COLUMN}' for the rubric slice analysis"
        )
    df = df.dropna(subset=[LABEL_COLUMN]).reset_index(drop=True)

    # 2. Preprocess
    X, y = preprocess_features(df)

    # 3. Final 70/30 train/test split. The tuner used only the 70% development
    # portion; this is the one and only evaluation of the held-out 30% test set.
    # test_size=0.30 matches the reference run and every published figure in
    # Lab 3 (6,999 train / 3,000 test on the 10k dataset). The bootstrap lift CI
    # is calibrated on a test set that size; a 0.20 split leaves 2,000 rows and
    # widens the interval enough to matter.
    #
    X_train, X_test, y_train, y_test, _tier_train, tier_test = train_test_split(
        X,
        y,
        df[SLICE_COLUMN],
        test_size=0.30,
        random_state=42,
        stratify=y,
    )
    print(f"\nFinal train: {len(X_train):,} | Final held-out test: {len(X_test):,}")

    # 4. Retrain the validation winner on the full 70% development split.
    model = train_model(X_train, y_train, args)

    # 5. Evaluate the final test set once, with strict promotion gates.
    baseline_proba = train_recency_baseline(X_train, y_train, X_test)
    metrics = evaluate_model(model, X_test, y_test, baseline_proba)
    metrics["evaluation_split"] = "final_test"
    metrics["n_test_samples"] = int(len(y_test))
    save_evaluation_artifacts(
        model,
        X_test,
        y_test,
        tier_test,
        args.output_data_dir,
    )

    # 6. Log the final run. It must exist before the same model is registered.
    final_tags = {
        "phase": "final",
        "data_source": data_source,
        "split": "70-train_30-final-test",
    }
    if winner.get("mlflow_run_id"):
        final_tags["source_tuning_run_id"] = str(winner["mlflow_run_id"])
    mlflow_run_id = track_run(
        args,
        metrics,
        getattr(args, "resolved_scale_pos_weight", None),
        tags=final_tags,
    )
    if mlflow_run_id:
        metrics["mlflow_run_id"] = mlflow_run_id

    # 7. Write the exact metrics used by MLflow and Model Registry.
    metrics_path = os.path.join(args.output_data_dir, "evaluation_metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"✓ Evaluation metrics: {metrics_path}")

    # 8. Save, upload, and register only after every strict evaluation gate passes.
    save_and_register_model(model, metrics, args)

    print("\n" + "=" * 60)
    print(f"Final training complete. Test AUC-ROC: {metrics['auc_roc']:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
