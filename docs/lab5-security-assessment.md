# NorthStar Churn Model: Security and Privacy Assessment

## Task 3

### 3a. STRIDE Threat Model

Scope is the weekly batch scoring pipeline for the churn model; Likelihood and Impact ratings are inherent risk, before mitigation.

| Threat | STRIDE Category | Likelihood (H/M/L) | Impact (H/M/L) | Mitigation | AWS Control |
|---|---|---|---|---|---|
| **S1.** A stolen long-lived access key for the human operator/admin IAM user is used to impersonate the operator (approve models, deploy, read data). | Spoofing | H: long-lived key, no MFA; credential theft is a leading real-world entry point | H: full account control | Replace IAM-user access keys with short-lived SSO credentials, enforce MFA, and detect anomalous API use. **Gap:** the three Lab 2 roles govern services, not human access, so none of them contains this threat. | AWS IAM Identity Center (SSO with enforced MFA); AWS STS temporary credentials; Amazon GuardDuty findings for anomalous credential use |
| **T1.** An approved `model.tar.gz` in `s3://<bucket>/artifacts/` is overwritten after approval, so the deployed model differs from the approved one. | Tampering | M: MLEngineer can write and delete under `artifacts/` | H: every customer is scored by an unapproved model | Lock approved artifacts and verify their integrity at deploy time. **Role:** MLEngineer contains the blast radius, as the only Lab 2 role with write access to `artifacts/` (DataEngineer can only read `artifacts/glue/`; ModelMonitor is read-only). | Amazon S3 Object Lock (governance mode) and S3 Versioning on the model artifact prefix; compare the object ETag to the SageMaker Model Registry package's `ModelDataETag` before creating the model; IAM policy limiting `artifacts/` writes to the training job |
| **R1.** An approver denies having approved a model version. | Repudiation | H: approvals are made through one shared admin identity, so they cannot be attributed to a person | M: no accountable approver for a governed release | Give every approver a per-person identity and keep an audit trail of every approval with a recorded reason. **Gap:** none of the Lab 2 roles holds approval rights, so approval happens outside the role boundary. | AWS CloudTrail management events for `sagemaker:UpdateModelPackage`; AWS IAM Identity Center per-person identities; SageMaker Model Registry `ApprovalDescription` |
| **I4.** Customer emails stored as unsalted SHA-256 hashes are re-identified with a dictionary attack (hash candidate emails and match). | Information Disclosure | M: emails are guessable | H: re-identifies customers; pseudonymized data is still personal data | Replace the plain hash with a keyed hash (HMAC) whose key never leaves KMS, and scan the bucket for exposed identifiers. **Gap:** any role that can read `raw/` (DataEngineer) can read the hashes; the roles control access, not the strength of pseudonymization. | AWS KMS HMAC keys (`GenerateMac`); Amazon Macie sensitive-data discovery jobs on the data bucket |
| **D5.** Malformed input rows (nulls, invalid values, wrong column count) crash the weekly transform job, so no scores are ready by the Monday 6 AM ET deadline. | Denial of Service | M: known data-quality issues in the sources | M: the week's campaign is delayed or runs on stale scores | Validate data before the feature job publishes, and alert on failed jobs. **Role:** DataEngineer contains the blast radius, as the only role that writes `processed/` and `features/` through the Glue pipeline. | AWS Glue Data Quality rulesets; Amazon EventBridge rule on SageMaker Transform Job State Change (`Failed`) with a notification |
| **E2.** The same engineer trains, registers and approves a model, bypassing separation of duties. | Elevation of Privilege | H: nothing prevents it today | H: an unreviewed or malicious model reaches Production | Create a dedicated approver role that alone may change approval status, and deny it to the role that trains and registers. **Gap:** MLEngineer can train and register, and no Lab 2 role separates the model's author from its approver. | IAM (dedicated approver role; explicit deny of `sagemaker:UpdateModelPackage` for MLEngineer); SageMaker Model Registry approval status; AWS CodePipeline manual approval action |

### 3b. Data Classification

Today every S3 prefix uses the bucket default S3-managed encryption (SSE-S3, AES256); the table gives the target state.

| Asset | Where it lives | Tier | Encryption (target) | Reason for encryption choice | IAM access policy | Third-party sharing |
|---|---|---|---|---|---|---|
| Customer PII | Source systems (CRM customer records, hashed email); lands in `raw/` and `processed/` only if ingested, and the churn pipeline does not ingest it | Restricted | SSE-KMS, data key (customer managed, automatic rotation) | Personal data, including pseudonymized hashes: the key policy adds decrypt control separate from S3 permissions, and CloudTrail logs every key use | DataEngineer reads/writes `raw/` and `processed/`; MLEngineer and ModelMonitor have no access. Data key policy grants decrypt on these prefixes only to DataEngineer. | No |
| Transaction history | `raw/` and `processed/` | Restricted | SSE-KMS, data key | Purchase history tied to a customer ID is personal data: key-policy decrypt control plus a CloudTrail audit trail of every decrypt | DataEngineer reads/writes; MLEngineer and ModelMonitor have no access. Data key policy grants decrypt on these prefixes only to DataEngineer. | No |
| Behavioral clickstream | Web event stream; lands in `raw/` and `processed/` only if ingested, and the churn pipeline does not ingest it | Restricted | SSE-KMS, data key | Clickstream and device data tied to a customer ID are personal data, and search queries can contain emails: key-policy decrypt control plus audit trail | DataEngineer reads/writes; MLEngineer and ModelMonitor have no access. Data key policy grants decrypt on these prefixes only to DataEngineer. | No |
| Product catalog | `raw/` (catalog file) | Public | SSE-S3 (per-object SSE-S3 override) | Public product information needs no confidentiality and no per-role decryption control, so S3-managed encryption at rest is sufficient | DataEngineer reads/writes; MLEngineer and ModelMonitor have no access. | Yes |
| Model weights | `artifacts/` (`model.tar.gz`), referenced by the SageMaker Model Registry | Restricted | SSE-KMS, model key (training job output KMS key) | Trained on personal data, so whether it contains personal data is assessed case by case: a separate key limits decrypt to the model's users and audits each load | MLEngineer reads/writes (training, batch transform); ModelMonitor reads only; DataEngineer has no access. Model key policy grants decrypt only to MLEngineer and ModelMonitor. | No |
| Inference logs | `datacapture/` (captured inputs and scores) and batch transform output | Restricted | SSE-KMS, inference key (capture and transform output KMS key) | Per-customer scores and inputs are outputs containing customer information: a separate key isolates them from training data and audits each read | Written by MLEngineer's jobs; read by MLEngineer and ModelMonitor (monitoring); DataEngineer has no access. Inference key policy grants decrypt only to MLEngineer and ModelMonitor. | No |
| Feature Store records | Offline store under `features/` and the online store, keyed by `customer_id` | Restricted | SSE-KMS, data key (offline store S3 key and online store key) | Per-customer features keyed by `customer_id` are personal data: key-policy decrypt control plus audit trail on both stores | DataEngineer reads/writes `features/` and writes records (`PutRecord`); MLEngineer reads/writes `features/` and reads records (`GetRecord`); ModelMonitor has no access. Data key policy grants decrypt on `features/` and the feature group only to DataEngineer and MLEngineer. | No |

## Task 4: Privacy Impact Assessment

### 1. Personal data inventory

Every stage is keyed by `customer_id`, a pseudonymous identifier. Pseudonymized data is still personal data, so every store below holds personal data. `<bucket>` is `northstar-dev-data-<account-id>`.

| Stage | Where | Personal data |
|---|---|---|
| Raw ingestion | `raw/customers/` in `<bucket>`, catalogued by the Glue crawler | One row per transaction: `customer_id`, `transaction_id`, `purchase_date`, `order_value`, `num_items`, `payment_method`, `channel`, `store_id`, `product_category` |
| Processing | `processed/customers/` (Parquet, written by the Glue transform job) | The same transaction fields, typed, imputed and deduplicated |
| Feature engineering | `features/customers/` (Parquet), and Feature Store feature group `northstar-dev-customer-features`: online store, and offline store under `features/offline-store/` | One row per customer: `customer_id`, the 11 purchase-behavior features, `loyalty_tier`, `churn_risk_score`, `churn_label`, `event_time`; the offline store keeps every ingested version of each record |
| Training | SageMaker training job reading `features/customers/`; model artifact `model.tar.gz` under `artifacts/training/` | The full labeled feature table; the trained model is derived from it |
| Batch scoring | `artifacts/lab5/batch/<run-id>/input/` and `artifacts/lab5/batch/<run-id>/output/{incumbent,candidate}/` | Input copy: `customer_id` and the 11 features; outputs: `customer_id` and each model's churn score |
| Inference logs | The batch score outputs above, and `datacapture/` when a real-time endpoint is used | Per-customer churn scores; for an endpoint, the captured request features and responses |

### 2. Lawful basis

**Legitimate interests (GDPR Article 6(1)(f)).**

- **Interest.** NorthStar has a legitimate commercial interest in retaining its existing customers and loyalty members: about 18% of active customers lapse each year, a $128.5M annual loss, and the model lets the retention team reach at-risk customers before they leave.
- **Necessity.** Purchase history is the minimal signal that predicts churn. Less intrusive means, such as sending the same offer to everyone or scoring nobody, cannot identify which customers are at risk.
- **Balancing test** (documented, following EDPB Opinion 28/2024 for AI model development). The data is what customers generated in their own relationship with NorthStar; loyalty members reasonably expect retention offers from a retailer they shop with; no special-category data is processed; identifiers are pseudonymized; the output is used only to send retention offers; and inputs are minimized (section 3).

Under GDPR Article 21, customers may object to this processing, and anyone who objects is excluded from scoring and from retention offers. Contractual necessity does not fit, because churn scoring is not needed to fulfil a purchase.

### 3. Data minimization

The model uses only the 11 purchase-behavior features in `FEATURE_COLUMNS`: recency, tenure, purchase frequency over 30, 90 and 180 days, average order value, 90-day and lifetime spend, basket size, category diversity, and online-to-store ratio. Data available in NorthStar's approved sources but excluded from training:

| Excluded data | Fields | Reason |
|---|---|---|
| Contact identifiers | hashed `email` | Not needed to meet the performance requirement; an unsalted hash can be re-identified (threat I4) |
| Demographics and location | `age_band`, `state`, `zip_code`, `store_id` | Not needed for performance; can act as proxies for protected characteristics |
| Payment and promotion details | `payment_method`, `promotion_code`, `promotion_discount` | Not needed for performance; adds legal and security exposure |
| Self-reported and account fields | `preferred_channel`, `loyalty_points`, `signup_date` | Not needed for performance; observed purchase behavior already covers channel and tenure |
| Clickstream, entirely | `session_id`, `device_type`, `search_query`, page and product events | Not needed for performance; adds tracking exposure, and search queries can contain emails |

Two fields stay in the feature table but are not model inputs: `loyalty_tier` is kept only to evaluate recall and fairness across tiers, and `churn_risk_score` is kept only as a rule-based reference.

### 4. Right to erasure workflow

The request for `customer_id = C00123456` is answered without undue delay and within one month (GDPR Articles 12(3) and 17). The data bucket is versioned, so every rewrite below ends by deleting the noncurrent versions that still contain the customer (S3 `ListObjectVersions`, then `DeleteObject` with each `VersionId`). Lifecycle rules would otherwise keep them for up to 30 days (`raw/`, `processed/`) or 60 days (`features/`), and `artifacts/` has no noncurrent-version expiry at all.

**(a) Raw S3 data.** Find the files in `raw/customers/` that contain the customer by querying the crawler's Glue Data Catalog table with Amazon Athena (`SELECT DISTINCT "$path" ... WHERE customer_id = 'C00123456'`). Rewrite each affected file without that customer's rows with a Glue job, and rewrite `processed/customers/` the same way (or rerun the Glue transform job, which overwrites that prefix from the cleaned raw data). Then delete the noncurrent versions of every affected object.

**(b) Feature Store.**

- **Online store.** Hard-delete the record. The default `SoftDelete` only nulls the features, and `EventTime` must be later than the record's own event time. `event_time` is a `Fractional` feature (Unix epoch seconds), so it is passed in the same format:

  ```python
  import time
  import boto3

  fs = boto3.client("sagemaker-featurestore-runtime")
  fs.delete_record(
      FeatureGroupName="northstar-dev-customer-features",
      RecordIdentifierValueAsString="C00123456",
      EventTime=str(time.time()),  # epoch seconds, the format the feature job writes; later than the record's event_time
      DeletionMode="HardDelete",
  )
  ```

- **Offline store.** `DeleteRecord` removes nothing here: it only appends a deletion-marker row, so the customer's earlier rows remain, and the marker also carries the ID. The offline store is a standard (non-Iceberg) Glue table of Parquet files under `features/offline-store/`. After the marker has landed, find the affected files with Athena (`"$path"` on the feature group's offline Glue table), rewrite them without the customer's rows with a Glue job or Athena CTAS, replace the originals, and delete their noncurrent versions. Rewrite `features/customers/` the same way. Because `processed/customers/` is already clean, the next feature job does not write the customer back.

**(c) Model training data.** Training reads `features/customers/`, so once (a) and (b) are done the customer is excluded from every future training set. The model is retrained on the documented weekly schedule, not immediately: retraining per request is costly, since each new model also needs approval and a parallel deployment run, and the next scheduled retrain still completes well within the one-month deadline. The retrained model goes through the normal approval and deployment plan. After it is promoted, delete the earlier model artifacts trained with the customer's data: their `model.tar.gz` objects under `artifacts/training/` (with noncurrent versions) and their SageMaker Models.

**(d) Inference logs.** Edit now rather than waiting for retention expiry. For every run folder under `artifacts/lab5/batch/` still within its 30-day retention, rewrite the input copy and both score outputs (`incumbent`, `candidate`) without the lines for `C00123456`, and do the same for any `datacapture/` files that contain it. Then delete the noncurrent versions of every rewritten object. Future runs build their input from the cleaned `features/customers/`.

**Hardest step:** (c), the trained model, because there is no record to delete. The customer's influence is spread across the model's learned tree splits and leaf weights, so removal is not a delete operation. The only reliable way to remove it is to retrain on data that excludes the customer, since machine unlearning is not yet reliable. Until the scheduled retrain is promoted, the deployed model still reflects their data.
