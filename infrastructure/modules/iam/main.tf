# ── modules/iam ──────────────────────────────────────────────────────────────
# Required resources (Task B1). Exactly one of each:
#
#   aws_iam_role                     MLEngineer, trusted by sagemaker.amazonaws.com
#   aws_iam_policy
#   aws_iam_role_policy_attachment
#
# Least privilege is graded in later labs, so start narrow: grant only the S3
# prefixes and SageMaker actions this role actually needs. A wildcard policy
# here will cost you points in Lab 2.

data "aws_iam_policy_document" "sagemaker_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ml_engineer" {
  name               = "${var.project}-${var.environment}-MLEngineer"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_trust.json

  tags = {
    Name = "${var.project}-${var.environment}-MLEngineer"
  }
}

resource "aws_iam_policy" "ml_engineer" {
  name        = "${var.project}-${var.environment}-NorthStarMLEngineerPolicy"
  description = "Policy for MLEngineer role"

  policy = jsonencode({
    "Version" : "2012-10-17",
    "Statement" : [
      {
        "Sid" : "SageMakerCore",
        "Effect" : "Allow",
        "Action" : [
          "sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:StopTrainingJob",
          "sagemaker:CreateEndpoint", "sagemaker:DescribeEndpoint", "sagemaker:DeleteEndpoint",
          "sagemaker:CreateEndpointConfig", "sagemaker:DeleteEndpointConfig",
          "sagemaker:CreateMlflowApp", "sagemaker:DescribeMlflowApp", "sagemaker:ListMlflowApps",
          "sagemaker:CreatePresignedMlflowAppUrl",
          "sagemaker:RegisterModel", "sagemaker:DescribeModelPackage", "sagemaker:ListModelPackages"
        ],
        "Resource" : "*"
      },
      {
        "Sid" : "StudioSelfService",
        "Effect" : "Allow",
        "Action" : [
          "sagemaker:DescribeDomain", "sagemaker:ListDomains",
          "sagemaker:DescribeUserProfile", "sagemaker:ListUserProfiles",
          "sagemaker:DescribeSpace", "sagemaker:ListSpaces", "sagemaker:CreateSpace",
          "sagemaker:UpdateSpace", "sagemaker:DeleteSpace",
          "sagemaker:DescribeApp", "sagemaker:ListApps", "sagemaker:CreateApp", "sagemaker:DeleteApp",
          "sagemaker:CreatePresignedDomainUrl"
        ],
        "Resource" : [
          "arn:aws:sagemaker:*:*:domain/*", "arn:aws:sagemaker:*:*:user-profile/*",
          "arn:aws:sagemaker:*:*:space/*", "arn:aws:sagemaker:*:*:app/*"
        ]
      },
      {
        "Sid" : "S3ArtifactsAndFeatures",
        "Effect" : "Allow",
        "Action" : ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        "Resource" : [
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/artifacts/*",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/features/*"
        ]
      },
      {
        "Sid" : "S3BucketList",
        "Effect" : "Allow",
        "Action" : ["s3:ListBucket", "s3:GetBucketLocation"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        "Sid" : "CloudWatchLogs",
        "Effect" : "Allow",
        "Action" : ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        "Resource" : "arn:aws:logs:*:*:log-group:/aws/sagemaker/*"
      },
      {
        "Sid" : "ECRRead",
        "Effect" : "Allow",
        "Action" : ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:GetAuthorizationToken"],
        "Resource" : "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ml_engineer" {
  role       = aws_iam_role.ml_engineer.name
  policy_arn = aws_iam_policy.ml_engineer.arn
}