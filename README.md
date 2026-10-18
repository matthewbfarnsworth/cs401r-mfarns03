# NorthStar Retail AI
Code repository for CS 401R, *Engineering the AI Enterprise*, at BYU.

NorthStar Retail is a fictional specialty retailer with 400 stores across North America and a growing e-commerce presence with ~$3.2B annual revenue.

## AI Systems
| System | Type | Business Goal |
|---|---|---|
| Churn Prediction | Batch ML (XGBoost) | Identify at-risk customers 90 days before churn |
| Offer Generation | LLM / RAG | Personalize retention offers |
| Customer Service Agent | Agentic AI | Handle inquiries and escalations autonomously |

## Repository Structure
```
.
|-- data/raw/             Sample transaction data
|-- docs/                 Architecture, contracts, lineage, and run evidence
|-- glue-scripts/         Transform and feature engineering jobs
|-- infrastructure/
|   |-- environments/     LocalStack and live AWS configurations
|   \-- modules/          Reusable Terraform modules
|-- scripts/              Verification, setup, and teardown tools
|-- Makefile              Local validation commands
\-- docker-compose.yml    LocalStack services
```

## Lab 2: Data and Feature Engineering

The data pipeline loads raw transaction CSV files into S3, discovers their schema with AWS Glue, writes cleaned transactions as Parquet, and produces customer-level features in both S3 and SageMaker Feature Store.

![NorthStar Lab 2 data lineage](docs/lab2-data-lineage.png)

The VPC module adds a private subnet and NAT Gateway for SageMaker and Glue. The IAM and storage modules add the DataEngineer and ModelMonitor roles, access boundaries, and S3 lifecycle rules. The Glue module creates the catalog database, crawler, private network connection, transform job, feature engineering job, and job script objects. The Feature Store module creates the 16-feature customer feature group with online and offline storage.

### Running the live pipeline

This requires Terraform, an authenticated AWS CLI, and `us-east-1` as the AWS region. The input may be any transaction CSV with the nine expected source columns. The included sample is the default example below.

Create or update the AWS infrastructure:

```bash
export AWS_DEFAULT_REGION=us-east-1
terraform -chdir=infrastructure/environments/dev init
terraform -chdir=infrastructure/environments/dev apply
```

Upload an input file and register its schema:

```bash
export INPUT_CSV=data/raw/northstar-raw-sample.csv
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="northstar-dev-data-${ACCOUNT_ID}"

aws s3 cp "${INPUT_CSV}" "s3://${BUCKET}/raw/customers/$(basename "${INPUT_CSV}")"
aws glue start-crawler --name northstar-dev-raw-crawler
aws glue get-crawler --name northstar-dev-raw-crawler \
  --query 'Crawler.State' --output text
```

Wait for the crawler to return `READY`, then run the transform job and wait for `SUCCEEDED`:

```bash
aws glue get-table --database-name northstar_dev --name customers
TRANSFORM_RUN_ID=$(aws glue start-job-run \
  --job-name northstar-dev-transform \
  --query JobRunId --output text)
aws glue get-job-run \
  --job-name northstar-dev-transform \
  --run-id "${TRANSFORM_RUN_ID}" \
  --query 'JobRun.JobRunState' --output text
aws s3 ls "s3://${BUCKET}/processed/customers/" --recursive
```

Run feature engineering after the transform succeeds:

```bash
FEATURE_RUN_ID=$(aws glue start-job-run \
  --job-name northstar-dev-feature-engineer \
  --query JobRunId --output text)
aws glue get-job-run \
  --job-name northstar-dev-feature-engineer \
  --run-id "${FEATURE_RUN_ID}" \
  --query 'JobRun.JobRunState' --output text
aws s3 ls "s3://${BUCKET}/features/customers/" --recursive
aws sagemaker describe-feature-group \
  --feature-group-name northstar-dev-customer-features \
  --query FeatureGroupStatus --output text
```

The status commands may be repeated until the crawler or job reaches its successful state.

### Running locally

LocalStack validates the supported Terraform resources without creating a NAT Gateway. It does not run the managed Glue or Feature Store pipeline.

```bash
make local-validate LOCAL_OUT=docs/lab2-localstack-output.txt
make local-destroy
```

### Testing

The live verification script requires `pandas` and `pyarrow`. Run it while the AWS resources and pipeline outputs still exist:

```bash
bash scripts/verify-lab2.sh 2>&1 | tee docs/lab2-verify-output.txt
```

### Teardown

Save the live verification output before teardown. Plain `terraform destroy` is not sufficient because Glue, SageMaker, and versioned S3 resources exist outside Terraform state.

```bash
bash scripts/teardown-lab2.sh
```

The script writes `docs/lab2-destroy-output.txt` and verifies that no billable Lab 2 resources remain.

## Lab 3: Model Development

The churn model trains locally with XGBoost against customer features queried from the SageMaker Feature Store offline store through Athena. Hyperparameter tuning uses a separate validation split and keeps the final 30% test set untouched. The selected model is retrained on the full development split, evaluated once on the test set, logged to the SageMaker MLflow App, and registered with `PendingManualApproval` status.

### Training and evaluation

This requires the Lab 2 infrastructure and feature pipeline to be running, an authenticated AWS CLI, and the project virtual environment. Resolve the account-specific resources first:

```bash
export AWS_DEFAULT_REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="northstar-dev-data-${ACCOUNT_ID}"
MLFLOW_APP_ARN=$(aws sagemaker list-mlflow-apps \
  --query "Summaries[?Name=='northstar-mlflow'].Arn | [0]" \
  --output text)
```

Run the validation grid without evaluating the held-out test set:

```bash
.venv/bin/python churn_tuning.py \
  --feature-group-name northstar-dev-customer-features \
  --artifacts-bucket "${BUCKET}" \
  --mlflow-arn "${MLFLOW_APP_ARN}" \
  --output-data-dir output/tuning
```

Run the selected configuration once against the final test set and capture the graded output:

```bash
mkdir -p docs/lab3-evidence
.venv/bin/python churn_training_skeleton.py \
  --feature-group-name northstar-dev-customer-features \
  --artifacts-bucket "${BUCKET}" \
  --mlflow-arn "${MLFLOW_APP_ARN}" \
  --winner-config output/tuning/tuning-winner.json \
  --output-data-dir docs/lab3-evidence \
  --model-dir model \
  --run-name xgb-final \
  2>&1 | tee docs/lab3-evidence/train-final.log
```

The final run writes its metrics, confusion matrix, feature-importance plot, and loyalty-tier evaluation to `docs/lab3-evidence/`. The evaluation discussion is in `docs/lab3-evaluation-report.md`.

### Track C: AgentCore customer service agent

Track C has its own Terraform root because AgentCore requires the AWS provider 6.x. It reuses the existing remote-state bucket and lock table but keeps separate state under `agent/terraform.tfstate`.

```bash
cd infrastructure/environments/agent
terraform init
terraform apply
cd ../../..
```

Run the gateway policy checks, then run the six-scenario agent evaluation at least twice:

```bash
source .venv/bin/activate
python agent/policy_tests.py
python evaluation_harness.py --track C
python evaluation_harness.py --track C
```

The demo notebook is `notebooks/lab3-demo.ipynb`. It runs the policy checks and all six agent scenarios against the live AgentCore stack. Run it with **Kernel → Restart & Run All** and save its outputs.

Track C writes evaluation CSVs to `evaluation_results/` and records tool traces and per-session costs in `docs/lab3-evidence/agent-traces.jsonl` and `docs/lab3-evidence/agent-cost.csv`. The fixed evidence file is `docs/lab3-evidence/agent_eval.csv`. Policy results are stored in `docs/lab3-evidence/policy-tests.json`, and observed limitations are documented in `docs/lab3-evidence/track-c-failures.md`.

### Evidence and teardown

Export AWS-side evidence after the final training and Track C evaluation, but before teardown:

```bash
bash scripts/collect-lab3-evidence.sh --track C
```

Fix every reported failure, then remove the Lab 3 resources and preserve the teardown gate:

```bash
bash scripts/teardown-lab3.sh
```

The teardown script writes `docs/lab3-teardown-output.txt`. The serverless MLflow App is retained for later labs.
