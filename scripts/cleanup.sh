#!/bin/bash
# cleanup.sh — run after each lab submission

echo "Destroying Terraform resources..."
cd infrastructure/environments/dev
terraform destroy -auto-approve

echo "Deleting any remaining SageMaker endpoints..."
aws sagemaker list-endpoints --query 'Endpoints[].EndpointName' --output text | \
  tr '\t' '\n' | \
  xargs -I{} aws sagemaker delete-endpoint --endpoint-name {}

echo "Done. Check the AWS Console to confirm no resources are running."