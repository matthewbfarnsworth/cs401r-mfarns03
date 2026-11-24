# NorthStar Churn Model Alert Specification

This document describes the alert set for the NorthStar churn model and its offer-generation pipeline. The alarms themselves are defined in Terraform in this directory: `alarms.tf` holds the metric alarms, `main.tf` holds the Glue failure rule and the alert topic, and `suppression.tf` holds the suppression rule. Every alarm name contains `northstar`, and every ModelLatency threshold is in microseconds.

## Severity tiers

A P1 alert means requests are failing now, so customers or the retention program are affected immediately. A P2 alert means serving is badly degraded or something will break before the next scoring cycle unless someone acts. A P3 alert is a problem to investigate during working hours. A P4 alert is a record that needs follow-up but no immediate action.

| # | Alert | Condition | Tier |
|---|---|---|---|
| 1 | `northstar-churn-5xx-error-rate-high` | More than 1% of endpoint requests, across both variants, return a 5XX error over 5 minutes. The 99.5% availability SLO allows 0.5% failures, so 1% means the error budget is burning at twice the allowed rate. | P1 |
| 2 | `northstar-churn-latency-p95-critical` | Champion p95 ModelLatency is above 500 ms (500000 µs) over 5 minutes. | P2 |
| 3 | `northstar-glue-job-failed` (EventBridge rule) | A run of `northstar-dev-transform` or `northstar-dev-feature-engineer` ends in FAILED, ERROR or TIMEOUT. | P2 |
| 4 | `northstar-churn-alert-volume-drop` | The daily count of churn alerts (scores of 0.5 or more) is more than 30% below the mean of the prior seven days. | P2 |
| 5 | `northstar-churn-latency-p95-high` | Champion p95 ModelLatency is above 200 ms (200000 µs) over 5 minutes. | P3 |
| 6 | `northstar-churn-cpu-p90-high` | Champion instance CPU p90 is above 80% over 5 minutes. | P3 |
| 7 | `northstar-churn-drift-detected` | The latest Evidently drift job flagged at least one monitored feature. | P3 |
| 8 | `northstar-churn-rollback-latency` | Canary p95 ModelLatency is above 200 ms (200000 µs) for 2 consecutive minutes. The deploy script reads this alarm and rolls the canary back. | P4 |

Server errors are P1 because they are the only alert where predictions are not being returned at all. The P2 alerts each describe damage that will reach the retention program soon if nobody acts. Severe latency slows every consumer of the endpoint, a failed Glue job leaves the next scoring cycle running on stale features, and a sharp drop in churn alerts shrinks the list the retention team works from, which usually means a broken model or input. The P3 alerts are early warnings whose effects play out over hours or days. The 200 ms latency warning and the CPU alarm fire before users notice any slowdown, and drift is expected during promotions and is checked against the promotion calendar before anyone acts on it. The canary rollback alarm is P4 because the deploy script already responds to it automatically, so a person only needs to record the rollback and investigate the canary later.

## Escalation per tier

| Tier | Escalation action | Response SLA |
|---|---|---|
| P1 | Wake the on-call engineer and page the engineering manager. | Acknowledged within 15 minutes, at any hour. |
| P2 | Page the on-call engineer. | Acknowledged within 30 minutes, at any hour. |
| P3 | Post to the team Slack channel and open a ticket. | Picked up within 240 minutes during working hours. |
| P4 | Open a ticket only. | Triaged by the end of the next business day. |

When a runbook escalates an incident beyond the on-call engineer, the incident moves up one tier and takes that tier's response SLA. The runbook names the additional role that is paged at that tier.

All alerts publish to the SNS topic `northstar-ml-alerts`. Each alert's description begins with its tier, and the Glue failure message begins with `P2`, so the paging, Slack and ticketing integrations subscribed to that topic route each notification by the tier at the start of the message. Each alarm also carries its tier in a `Severity` tag. The canary rollback alarm is the exception. It has no notification action, no `Severity` tag and no tier at the start of its description, because its definition is kept exactly as the deploy script creates it, and the P4 ticket is opened when the deploy script reports a rollback. Two alarms in `suppression.tf`, `northstar-churn-deployment-in-progress` and `northstar-churn-serving-suppressor`, are not alerts. They send no notifications and have no tier, because they exist only to drive the suppression rule.

## Suppression rule

While an endpoint deployment is in progress, or while the P1 error-rate alarm is firing, the latency and CPU alerts (alerts 2, 5 and 6) are suppressed.

Both conditions produce an alert storm that adds nothing. A deployment causes cold starts and replaces instances, so latency and CPU rise on a healthy rollout. During an outage, the P1 error-rate page already identifies the root cause, and the latency and CPU alerts that come with it only add noise.

The rule is implemented with CloudWatch composite alarms in `suppression.tf`. The latency and CPU alarms have no actions of their own. Instead, `northstar-churn-serving-p2` (alert 2) and `northstar-churn-serving-p3` (alerts 5 and 6) send their notifications. Both composites name `northstar-churn-serving-suppressor` as their actions suppressor. That composite is in ALARM when either `northstar-churn-deployment-in-progress` or `northstar-churn-5xx-error-rate-high` is in ALARM. The deployment signal comes from `deployment/configs/canary_deploy_realtime.py`, which publishes `DeploymentInProgress = 1` to `NorthStar/ChurnModel` roughly every 30 seconds while it creates or updates the endpoint. When publishing stops, CloudWatch waits several minutes for late data and then treats the missing data as not breaching, so the deployment alarm returns to OK on its own (about 7 minutes after the last heartbeat in testing) and a crashed deployment cannot leave alerts suppressed indefinitely.

When a serving composite goes into ALARM, it waits up to 5 minutes for the suppressor, which is one evaluation period of the error-rate alarm, so an outage that begins together with a latency spike still suppresses it. The cost is that every P2 and P3 serving notification is sent 5 minutes after the composite enters ALARM, and a composite that returns to OK within those 5 minutes sends nothing. After the suppressor clears, suppression continues for another 10 minutes, which is two 5-minute periods, so the first window after a deployment, which still contains cold-start requests, does not notify. The suppressed alarms keep changing state and stay visible on the NorthStar-AI-Platform dashboard. Only their notifications are held back.

## Observer and execution identities

The platform has two monitoring roles, and they are deliberately different. `northstar-dev-ModelMonitor` is the observer identity. It is read-only, with no S3 write access and no ECR pull, and it is meant for the people and automation that watch dashboards, alarms and reports. `northstar-dev-ModelMonitorExecution` is the execution identity. The Evidently drift job runs as this role because it must pull its container from ECR, read the capture and baseline, and write `drift_report.json` and `drift_violations.json` under `monitoring/`. If the drift job is given the observer role instead, it fails several minutes in with an opaque `Failed` status. Keeping the roles separate means that watching the platform never grants the ability to change what the alerts are based on.
