#!/usr/bin/env bash
# Churn batch scoring, step 1: start a run by building its input file.
#
# One header-less CSV line per customer: customer_id, then the model features
# in FEATURE_COLUMNS order (models/churn/churn_model.py). The transform job's
# InputFilter hides customer_id from the model, and its JoinSource/OutputFilter
# carry it through to the output, so every score stays attached to its customer.
#
# Usage: bash scripts/churn-batch-prepare-input.sh [run-id]
#        run-id defaults to the current UTC time (YYYYMMDD-HHMMSS).
source "$(dirname "$0")/churn-batch-common.sh"

RUN_ID="${1:-$(new_run_id)}"
DEST="$(run_input_uri "$RUN_ID")customers.csv"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

aws s3 cp "s3://${BUCKET}/features/customers/" "$WORK/features/" --recursive --quiet

"$PY" - "$WORK" <<'PY'
import glob
import sys

import pandas as pd

sys.path.insert(0, "models/churn")
import churn_model  # noqa: E402

work = sys.argv[1]
parts = glob.glob(f"{work}/features/**/*.parquet", recursive=True)
if not parts:
    sys.exit("no feature parquet files found")
df = pd.concat(pd.read_parquet(p) for p in parts)

features = list(churn_model.FEATURE_COLUMNS)
out = df[["customer_id"] + features].copy()
out[features] = out[features].astype(float)

if out["customer_id"].duplicated().any():
    sys.exit("duplicate customer_id values in features; refusing to build input")

missing = int(out[features].isna().sum().sum())
out.to_csv(f"{work}/customers.csv", header=False, index=False)
print(f"customers: {len(out)}   columns: customer_id + {len(features)} features")
print(f"missing feature values: {missing}")
PY

aws s3 cp "$WORK/customers.csv" "$DEST" --quiet
echo "uploaded ${DEST}"
echo "first line:"
head -1 "$WORK/customers.csv"
echo "run id: ${RUN_ID}"
