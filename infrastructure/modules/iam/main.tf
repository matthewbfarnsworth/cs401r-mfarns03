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

data "aws_iam_policy_document" "data_engineer_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type = "Service"
      identifiers = [
        "glue.amazonaws.com",
        "lambda.amazonaws.com",
        "sagemaker.amazonaws.com"
      ]
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
      },
      {
        "Sid" : "FeatureStoreRead",
        "Effect" : "Allow",
        "Action" : [
          "sagemaker:DescribeFeatureGroup",
          "sagemaker:GetRecord",
          "sagemaker:BatchGetRecord"
        ],
        "Resource" : "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ml_engineer" {
  role       = aws_iam_role.ml_engineer.name
  policy_arn = aws_iam_policy.ml_engineer.arn
}

resource "aws_iam_role" "data_engineer" {
  name               = "${var.project}-${var.environment}-DataEngineer"
  assume_role_policy = data.aws_iam_policy_document.data_engineer_trust.json

  tags = {
    Name = "${var.project}-${var.environment}-DataEngineer"
  }
}

resource "aws_iam_policy" "data_engineer" {
  name        = "${var.project}-${var.environment}-NorthStarDataEngineerPolicy"
  description = "Policy for the NorthStar DataEngineer role"

  policy = jsonencode({
    "Version" : "2012-10-17",
    "Statement" : [
      {
        "Sid" : "GlueControlPlane",
        "Effect" : "Allow",
        "Action" : ["glue:*"],
        "Resource" : "*"
      },
      {
        "Sid" : "GlueNetworking",
        "Effect" : "Allow",
        "Action" : [
          "ec2:CreateNetworkInterface",
          "ec2:DeleteNetworkInterface",
          "ec2:Describe*"
        ],
        "Resource" : "*"
      },
      {
        "Sid" : "GlueNetworkInterfaceTags",
        "Effect" : "Allow",
        "Action" : [
          "ec2:CreateTags",
          "ec2:DeleteTags"
        ],
        "Resource" : "arn:aws:ec2:*:*:network-interface/*"
      },
      {
        "Sid" : "DataBucketMetadata",
        "Effect" : "Allow",
        "Action" : [
          "s3:GetBucketLocation",
          "s3:GetBucketAcl",
          "s3:ListBucketMultipartUploads"
        ],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        "Sid" : "ListDataPrefixes",
        "Effect" : "Allow",
        "Action" : ["s3:ListBucket"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*",
        "Condition" : {
          "StringLike" : {
            "s3:prefix" : [
              "raw",
              "raw/*",
              "processed",
              "processed/*",
              "features",
              "features/*",
              "artifacts/glue",
              "artifacts/glue/*"
            ]
          }
        }
      },
      {
        "Sid" : "DataZonesReadWrite",
        "Effect" : "Allow",
        "Action" : [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ],
        "Resource" : [
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/raw/*",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/processed/*",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/features/*"
        ]
      },
      {
        "Sid" : "FeatureStoreObjectAcls",
        "Effect" : "Allow",
        "Action" : ["s3:PutObjectAcl"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*/features/*"
      },
      {
        "Sid" : "GlueScriptsRead",
        "Effect" : "Allow",
        "Action" : ["s3:GetObject"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*/artifacts/glue/*"
      },
      {
        "Sid" : "FeatureStoreWrite",
        "Effect" : "Allow",
        "Action" : [
          "sagemaker:PutRecord",
          "sagemaker:CreateFeatureGroup",
          "sagemaker:DescribeFeatureGroup"
        ],
        "Resource" : "*"
      },
      {
        "Sid" : "CloudWatchLogs",
        "Effect" : "Allow",
        "Action" : [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        "Resource" : "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "data_engineer" {
  role       = aws_iam_role.data_engineer.name
  policy_arn = aws_iam_policy.data_engineer.arn
}

resource "aws_iam_role" "model_monitor" {
  name               = "${var.project}-${var.environment}-ModelMonitor"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_trust.json

  tags = {
    Name = "${var.project}-${var.environment}-ModelMonitor"
  }
}

resource "aws_iam_policy" "model_monitor" {
  name        = "${var.project}-${var.environment}-NorthStarModelMonitorPolicy"
  description = "Policy for the NorthStar ModelMonitor role"

  policy = jsonencode({
    "Version" : "2012-10-17",
    "Statement" : [
      {
        "Sid" : "CloudWatchMetrics",
        "Effect" : "Allow",
        "Action" : [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DescribeAlarms"
        ],
        "Resource" : "*"
      },
      {
        "Sid" : "ProcessingJobVisibility",
        "Effect" : "Allow",
        "Action" : [
          "sagemaker:ListProcessingJobs",
          "sagemaker:DescribeProcessingJob"
        ],
        "Resource" : "*"
      },
      {
        "Sid" : "ArtifactBucketMetadata",
        "Effect" : "Allow",
        "Action" : ["s3:GetBucketLocation"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        "Sid" : "ListArtifacts",
        "Effect" : "Allow",
        "Action" : ["s3:ListBucket"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*",
        "Condition" : {
          "StringLike" : {
            "s3:prefix" : [
              "artifacts",
              "artifacts/*"
            ]
          }
        }
      },
      {
        "Sid" : "ReadArtifacts",
        "Effect" : "Allow",
        "Action" : ["s3:GetObject"],
        "Resource" : "arn:aws:s3:::${var.project}-${var.environment}-data-*/artifacts/*"
      },
      {
        "Sid" : "CloudWatchLogs",
        "Effect" : "Allow",
        "Action" : [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        "Resource" : "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "model_monitor" {
  role       = aws_iam_role.model_monitor.name
  policy_arn = aws_iam_policy.model_monitor.arn
}