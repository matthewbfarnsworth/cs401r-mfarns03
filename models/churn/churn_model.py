"""Your Lab 3 churn model, packaged for the Lab 4 pipeline.

YOU WRITE THIS FILE. The pipeline's training job runs train_sagemaker.py
(beside this file; do not edit it), which imports this module and calls
train(). Everything the job needs from you goes through the three names below.

Contract (train_sagemaker.py checks it in the first seconds of the job):

    FEATURE_COLUMNS   list[str]   model input columns, in order
    LABEL             str         "churn_label"
    train(df, seed=42) -> (xgboost.Booster, dict)

`df` is the full labeled feature table: every column of your Lab 2 output,
including `loyalty_tier` for slice evaluation. It is already loaded. Do not
query Athena or the Feature Store from here; the training container stages
the data for you and has no business making service calls for its input.

The metrics dict must include the four numbers the CI gate in
tests/test_model.py reads. Your Lab 3 evaluate_model() already returns them:

    precision_top10, recall_top10      gated at 0.50 / 0.25
    lift_ci_low, lift_ci_high          bootstrap 95% CI on AUC lift over the
                                       recency-only baseline; lift_ci_low > 0

Include anything else you reported in Lab 3 (auc_roc, baseline_auc,
positive_rate_val, slice results). It is all written to evaluation_metrics.json
and attached to the registered model.

What runs in the container: the SageMaker XGBoost 1.7-1 image ships pandas,
numpy, scikit-learn, xgboost and boto3. It does NOT ship mlflow or sagemaker,
so do not import them at the top of this file. MLflow tracking stays in your
Lab 3 script.

Only this directory (models/churn/) is uploaded to the training job, so the
code must live here. `import churn_training_skeleton` will fail inside the
container even though it works on your laptop. Copy the functions you need.

Test it locally before pushing:

    python models/churn/train_sagemaker.py \
        --train-channel <dir containing your features CSV or Parquet> \
        --model-dir /tmp/model --output-data-dir /tmp/out
"""

# Keeps Lab 3 annotations like `-> tuple[np.ndarray, np.ndarray]` from being
# evaluated, whatever Python the training container runs.
from __future__ import annotations
from datetime import datetime
import numpy as np  # noqa: F401
import pandas as pd  # noqa: F401
import xgboost as xgb  # noqa: F401
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

# Must match the columns your Lab 3 model was trained on, in the same order.
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

LABEL = "churn_label"
LABEL_COLUMN = LABEL
BASELINE_FEATURE = "days_since_last_purchase"
SLICE_COLUMN = "loyalty_tier"
HYPERPARAMS = {
    "max_depth": 6,
    "min_child_weight": 10,
    "subsample": 0.6,
    "colsample_bytree": 1.0,
    "eta": 0.1,
}
NUM_ROUNDS = 35

EVAL_THRESHOLDS = {
    "precision_top10":   0.50,
    "recall_top10":      0.25,
}
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42

def preprocess_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:

    print(f"Preprocessing {len(df):,} samples...")
    df = df.dropna(subset=[LABEL_COLUMN])
    X = df[FEATURE_COLUMNS].fillna(-1).values
    y = df[LABEL_COLUMN].values.astype(int)

    print(f"  Positive class rate: {y.mean():.1%}")
    print(f"  Feature matrix shape: {X.shape}")

    return X, y

def predict_model(model: xgb.Booster, data: xgb.DMatrix) -> np.ndarray:
    if hasattr(model, "best_iteration"):
        return model.predict(
            data,
            iteration_range=(0, model.best_iteration + 1),
        )
    return model.predict(data)

def top10_selection(y_pred_proba: np.ndarray) -> tuple[float, np.ndarray]:
    threshold = float(np.percentile(y_pred_proba, 90))
    return threshold, y_pred_proba >= threshold

def train_recency_baseline(X_train: np.ndarray,
                           y_train: np.ndarray,
                           X_val: np.ndarray) -> np.ndarray:
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
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = predict_model(model, dval)

    auc_roc = roc_auc_score(y_val, y_pred_proba)

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

def evaluate_slices(model: xgb.Booster,
                    X_val: np.ndarray,
                    y_val: np.ndarray,
                    slice_column: pd.Series) -> list[dict]:
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

def train(df: pd.DataFrame, seed: int = 42):
    """Train the churn model on `df` and return (booster, metrics).

    TODO (Lab 4): implement this from your Lab 3 code. The usual shape:

      1. X, y from FEATURE_COLUMNS and LABEL (handle missing values the way
         your Lab 3 preprocess_features() did)
      2. train_test_split(..., test_size=0.30, random_state=seed, stratify=y)
         -- carry the tier through the same call if you report slices. Pass
         df["loyalty_tier"] (a Series) or .to_numpy(dtype=object); NOT
         .values, which under pandas 3 is an Arrow array that
         train_test_split cannot index
      3. train the XGBoost model (your Lab 3 train_model())
      4. train the recency-only baseline (train_recency_baseline())
      5. metrics = evaluate_model(...)   # keep Lab 3's raise on a failed gate:
                                         # it fails the training step, so a
                                         # model that misses a gate is never
                                         # registered
      6. return model, metrics

    Return an xgboost.Booster (what xgb.train() gives you). If you used
    XGBClassifier, return clf.get_booster().
    """
    df = df.dropna(subset=[LABEL_COLUMN]).reset_index(drop=True)
    X, y = preprocess_features(df)

    X_train, X_test, y_train, y_test, _tier_train, tier_test = train_test_split(
        X, y, df[SLICE_COLUMN], test_size=0.30, random_state=seed, stratify=y
    )

    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    params = {
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "scale_pos_weight": spw,
        "seed": seed,
        **HYPERPARAMS
    }
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    model = xgb.train(params, dtrain, num_boost_round=NUM_ROUNDS)
    baseline_proba = train_recency_baseline(X_train, y_train, X_test)
    metrics = evaluate_model(model, X_test, y_test, baseline_proba)
    metrics["evaluation_split"] = "final_test"
    metrics["n_test_samples"] = int(len(y_test))
    metrics["slices"] = evaluate_slices(model, X_test, y_test, tier_test)
    
    return model, metrics
