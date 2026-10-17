"""Tune the NorthStar churn model without touching the held-out test set.

This script runs the agreed 3 x 3 x 3 x 3 grid on a 56/14/30
train/validation/test split. Every configuration is logged as its own MLflow
run. Only configurations that pass all validation gates are eligible. The
eligible configuration that captures the most churners in the validation-set
top 10% is selected; validation AUC breaks a tie.

It does not register a model. The separate final training script retrains the
winner on the full 70% development split and evaluates the 30% test split once.
"""

import argparse
import itertools
import json
import os
from types import SimpleNamespace

import pandas as pd
from sklearn.model_selection import train_test_split

from churn_training_skeleton import (
    evaluate_model,
    load_features_from_feature_store,
    load_features_local,
    preprocess_features,
    track_run,
    train_model,
    train_recency_baseline,
)


MAX_DEPTHS = (4, 6, 8)
MIN_CHILD_WEIGHTS = (1, 5, 10)
SUBSAMPLES = (0.6, 0.8, 1.0)
COLSAMPLE_BYTREES = (0.6, 0.8, 1.0)

ETA = 0.1
MAX_NUM_ROUND = 200
RANDOM_SEED = 42


def parse_args():
    parser = argparse.ArgumentParser(
        description="Tune the NorthStar XGBoost churn model (81 MLflow runs)"
    )
    parser.add_argument(
        "--feature-group-name",
        default="northstar-dev-customer-features",
    )
    parser.add_argument("--training-start-date", default="2026-01-01")
    parser.add_argument("--training-end-date", default="2027-01-01")
    parser.add_argument(
        "--artifacts-bucket",
        help="Required for the Feature Store/Athena loader; unnecessary with local data",
    )
    parser.add_argument(
        "--local-data-path",
        help="Development-only local feature CSV; the graded final run must use Athena",
    )
    parser.add_argument(
        "--mlflow-arn",
        default=os.environ.get("MLFLOW_APP_ARN"),
        help="SageMaker MLflow App ARN (not a Tracking Server ARN)",
    )
    parser.add_argument("--mlflow-experiment", default="northstar-churn")
    parser.add_argument(
        "--output-data-dir",
        default="./output/tuning",
        help="Local progress CSV and winning-configuration JSON directory",
    )
    parser.add_argument(
        "--allow-untracked",
        action="store_true",
        help="Allow a development grid without MLflow; never use for the graded grid",
    )
    return parser.parse_args()


def save_results(rows: list[dict], path: str) -> None:
    """Checkpoint completed configurations so an interrupted grid is inspectable."""
    pd.DataFrame(rows).to_csv(path, index=False)


def main():
    args = parse_args()
    if not args.mlflow_arn and not args.allow_untracked:
        raise ValueError(
            "The 81-run grid must be tracked. Supply --mlflow-arn (or set "
            "MLFLOW_APP_ARN). Use --allow-untracked only for a development test."
        )
    if not args.local_data_path and not args.artifacts_bucket:
        raise ValueError(
            "--artifacts-bucket is required when loading from Feature Store/Athena"
        )

    os.makedirs(args.output_data_dir, exist_ok=True)
    results_path = os.path.join(args.output_data_dir, "tuning-results.csv")
    winner_path = os.path.join(args.output_data_dir, "tuning-winner.json")

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

    X, y = preprocess_features(df)

    # Outer split creates the untouched final test set. The tuner never scores it.
    X_dev, X_test, y_dev, y_test = train_test_split(
        X,
        y,
        test_size=0.30,
        random_state=RANDOM_SEED,
        stratify=y,
    )
    # 20% of the 70% development split is 14% of all rows: 56/14/30 overall.
    X_train, X_val, y_train, y_val = train_test_split(
        X_dev,
        y_dev,
        test_size=0.20,
        random_state=RANDOM_SEED,
        stratify=y_dev,
    )
    print(
        f"Train: {len(X_train):,} | Validation: {len(X_val):,} | "
        f"Test held untouched: {len(X_test):,}"
    )
    # Make the non-use of test labels explicit: only their count was printed.
    del X_test, y_test

    baseline_proba = train_recency_baseline(X_train, y_train, X_val)
    grid = list(itertools.product(
        MAX_DEPTHS,
        MIN_CHILD_WEIGHTS,
        SUBSAMPLES,
        COLSAMPLE_BYTREES,
    ))
    results = []

    for run_number, (depth, child_weight, subsample, colsample) in enumerate(
        grid, start=1
    ):
        run_name = (
            f"tune-{run_number:03d}-depth{depth}-child{child_weight}-"
            f"sub{subsample}-col{colsample}"
        )
        print(f"\n{'=' * 72}\nRun {run_number}/{len(grid)}: {run_name}\n{'=' * 72}")
        run_args = SimpleNamespace(
            max_depth=depth,
            eta=ETA,
            num_round=MAX_NUM_ROUND,
            min_child_weight=child_weight,
            subsample=subsample,
            colsample_bytree=colsample,
            scale_pos_weight=None,
            mlflow_arn=args.mlflow_arn,
            mlflow_experiment=args.mlflow_experiment,
            run_name=run_name,
        )

        model = train_model(X_train, y_train, run_args, X_val, y_val)
        metrics = evaluate_model(
            model,
            X_val,
            y_val,
            baseline_proba,
            enforce_thresholds=False,
        )
        best_iteration = int(model.best_iteration)
        effective_num_round = best_iteration + 1
        metrics["best_iteration"] = best_iteration
        metrics["effective_num_round"] = effective_num_round

        mlflow_run_id = track_run(
            run_args,
            metrics,
            run_args.resolved_scale_pos_weight,
            tags={
                "phase": "tuning",
                "data_source": data_source,
                "split": "56-train_14-validation_30-test-held-out",
                "selection_rule": "max-top10-true-positives_then-auc",
            },
        )
        row = {
            "run_number": run_number,
            "run_name": run_name,
            "mlflow_run_id": mlflow_run_id,
            "max_depth": depth,
            "min_child_weight": child_weight,
            "subsample": subsample,
            "colsample_bytree": colsample,
            "eta": ETA,
            "max_num_round": MAX_NUM_ROUND,
            "scale_pos_weight": run_args.resolved_scale_pos_weight,
            **metrics,
        }
        results.append(row)
        save_results(results, results_path)

    eligible = [row for row in results if row["passed_gates"] == 1]
    if not eligible:
        raise RuntimeError(
            f"All {len(results)} configurations failed at least one validation gate. "
            f"Inspect {results_path}; do not evaluate the test set."
        )

    most_churners = max(row["top10_true_positives"] for row in eligible)
    churner_leaders = [
        row for row in eligible
        if row["top10_true_positives"] == most_churners
    ]
    highest_auc = max(row["auc_roc"] for row in churner_leaders)
    winners = [
        row for row in churner_leaders
        if row["auc_roc"] == highest_auc
    ]
    if len(winners) != 1:
        tie_path = os.path.join(args.output_data_dir, "tuning-tie-candidates.json")
        with open(tie_path, "w") as handle:
            json.dump(winners, handle, indent=2)
        raise RuntimeError(
            f"{len(winners)} eligible configurations captured {most_churners} "
            f"validation churners and tied at validation AUC {highest_auc:.8f}. "
            f"No further tie-breaker was authorized. "
            f"Review {tie_path} and choose explicitly; the test set remains untouched."
        )

    winner = winners[0]
    with open(winner_path, "w") as handle:
        json.dump(winner, handle, indent=2)

    print("\nWinning validation configuration")
    print(
        "Selection rule: most top-10% churners among gate-passing runs; "
        "higher validation AUC breaks a tie."
    )
    print(json.dumps(winner, indent=2))
    print(f"\nResults: {results_path}")
    print(f"Winner:  {winner_path}")
    print("The 30% test set was not evaluated.")


if __name__ == "__main__":
    main()
