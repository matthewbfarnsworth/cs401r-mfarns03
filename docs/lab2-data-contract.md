## Data Contract: `processed/customers/`

### Producer

Team / process: Glue ETL job `northstar-dev-transform`

### Consumers

- Feature engineering job `northstar-dev-feature-engineer`
- Future direct model training in Lab 3

### Grain

One row represents one transaction. A customer may appear in multiple rows. `transaction_id` is the unique record key.

### Schema

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `transaction_id` | string | No | Unique transaction identifier in the form `TXN-{12 alphanumeric characters}`. |
| `customer_id` | string | No | Customer identifier in the form `CUST-{8 digits}`; it may repeat across transactions. |
| `purchase_date` | date | No | Transaction date normalized to the ISO 8601 `YYYY-MM-DD` representation. |
| `order_value` | double | No | Gross order value in USD; a missing raw value is replaced with the column median. |
| `num_items` | integer | No | Number of line items in the order; a missing raw value is replaced with the rounded column median. |
| `payment_method` | string | No | Payment method: `credit_card`, `debit_card`, `gift_card`, `cash`, or `unknown`. |
| `channel` | string | No | Purchase channel: `store`, `online`, or `unknown`. |
| `store_id` | string | No | Store identifier in the form `STORE-{3 digits}`, `ONLINE`, or `unknown`. |
| `product_category` | string | No | Primary product category, or `unknown` when the raw value is missing. |

### Quality Guarantees

- `customer_id` is never null or blank.
- `transaction_id` is never null or blank, and no two rows have the same `transaction_id`.
- Numeric values remain within their expected ranges: `order_value` is in `[0.0, +infinity)`, and `num_items` is an integer in `[1, +infinity)`.
- `purchase_date` is never null and is a valid ISO 8601 date represented as `YYYY-MM-DD`.
- `payment_method` is one of `credit_card`, `debit_card`, `gift_card`, `cash`, or `unknown`.
- `channel` is one of `store`, `online`, or `unknown`.
- `store_id` matches `STORE-{3 digits}`, is `ONLINE`, or is `unknown`.

### SLA

- Queryable Parquet data is available in `processed/customers/` within 2 hours of the successful arrival of source data in `raw/customers/`.

### Versioning

- Schema changes require a new S3 prefix, such as `processed/customers/v2/`.
- Breaking changes require consumer notification at least 5 business days in advance.
