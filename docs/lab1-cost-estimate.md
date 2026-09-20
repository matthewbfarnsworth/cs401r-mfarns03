# Monthly Cost Estimate

| Component | Monthly Estimate | Key Assumptions | One Optimization |
|---|---|---|---|
| SageMaker Studio | $49.00 | 6 engineers, 8 hrs/day (22 days/mo = 1,056 hrs) at $0.0464/hr | Auto-shutdown after 60 min idle stops 24/7 runs, saving $154.23/mo (76% compute reduction) |
| S3 storage | $47.10 | 2,048 GB (2 TB) across 4 prefixes at $0.023/GB | S3 Lifecycle transition to Glacier for data >90 days saves $19.86/mo per TB |
| Internet Gateway | $2.00 | 200 GB egress at $0.01/GB data transfer | VPC endpoints for S3/ECR in Lab 2 eliminate internet egress charges |
| DynamoDB (state lock) | $0.01 | On-demand (PAY_PER_REQUEST), ~5,000 lock checks/mo, <1 MB storage | Keep PAY_PER_REQUEST to avoid paying for unused provisioned capacity |
| S3 state bucket | $0.01 | Minimal storage (<100 MB versioned state) | Expire noncurrent state versions older than 30 days to limit version sprawl |
| **Total** | **$98.12** | | Combined optimizations save up to $174.09/mo across compute and storage |