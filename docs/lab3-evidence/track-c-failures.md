# Track C Failure Cases

## 1. Two credits can bypass the $50 cap

`agent/policy_tests.py` called `apply_loyalty_credit` twice for the same customer. Each call requested $40. The gateway allowed both calls, so the customer could receive $80 even though the intended cap is $50.

The Cedar rule only sees the current request. Each $40 request is below $50, so both requests are valid when checked separately. A stateless per-call rule cannot add the current amount to credits issued earlier.

The simplest proposed fix is an AgentCore temporal policy that adds the credit amounts within one policy session and denies the call that would make the total exceed $50. This would require every gateway call to carry the same `x-amzn-bedrock-agentcore-policy-session-id`, and the gateway role would need `bedrock-agentcore:GetWorkloadAccessToken`. The history would still apply only within that session; a production system that needs a customer-wide or daily cap should store the running total in a durable customer ledger and update it atomically.

The current Harness setup cannot supply the required policy-session header. The Harness gateway tool configuration exposes the gateway ARN and outbound authentication, but not custom policy-session headers. Therefore, the temporal policy is not supported by this Harness configuration today. A direct Gateway or supported Runtime client could provide the header.

Evidence: the `split credit, part 1 of 2` and `split credit, part 2 of 2` entries in `policy-tests.json` both have `actual: ALLOW`.

AWS references:

- [Temporal policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal.html)
- [Harness AgentCore Gateway configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_HarnessAgentCoreGatewayConfig.html)

## 2. Escalation was stated but the tool was not called

TC-003 and TC-005 passed the supplied evaluator because their responses used escalation language. However, their traces contain `query_policy` calls and no `escalate_to_human` call. Saying that a case will be escalated does not create a real support case, so this is a functional failure even though the text sounds correct.

Affected trace sessions from the two evaluation runs:

- `test-TC-003-1792299057-9179c35d-cce3-40cb-b71d-fb85d554a554`
- `test-TC-005-1792299070-8228d0fd-98ff-474f-b00c-54518ca2eab6`
- `test-TC-003-1792299128-db4c10b5-7c13-4b1a-a455-e1babb40aa73`
- `test-TC-005-1792299139-71c76873-dbe0-4b81-9a07-79587bc5e6f3`

A concrete production fix is to make escalation completion depend on the tool result. The orchestration layer should not report that a case was escalated unless `escalate_to_human` succeeded. The evaluator should also require that tool name in the trace instead of accepting escalation language alone. I did not change the starter prompt or tool definitions to hide this failure.
