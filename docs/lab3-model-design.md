# Track Selection

I chose Track C, **Customer Service Agent**, since I am already working on a RAG pipeline for my job and wanted the experience of making a ReAct agent.

## Churn model

I used gradient-boosted trees for the NorthStar churn model. The dataset has 9,999 customers and 13 available tabular features from the feature group. Eleven of those fields are model inputs. The churn heuristic is kept out so it can serve as the baseline, and loyalty tier is used for slice evaluation instead of as a model input. This is a good size and shape for gradient boosting. Trees can learn nonlinear relationships between recency, spending, frequency, and customer behavior without needing a very large dataset.

Logistic regression would be simpler, but it assumes a mostly linear relationship unless I manually create interaction terms. That is limiting here. A long time since the last purchase is useful, but the meaning can change based on purchase frequency, spending, and customer tenure. Gradient boosting can learn those interactions directly. A neural network would add more tuning and make the model harder to explain without a clear benefit on a dataset of only about 10,000 rows.

Interpretability also matters because the retention team needs to understand why customers are being selected. XGBoost gain importance gives the team a useful high-level view of which features affected the model. It does not prove causation, but it is easier to explain than a neural network. I would switch to logistic regression if it produced nearly the same ranking quality, since the simpler model would be easier to maintain and calibrate. I would consider a neural model if NorthStar had much more data, especially sequences of purchases, customer messages, or other unstructured inputs.

## What the features added

The recency-only baseline reached an AUC of 0.7233. The full model reached 0.7785, an increase of 0.0552. The paired 95% confidence interval was [0.0354, 0.0727], so the improvement was consistently above zero. The lift is not huge, but it is real. At the top-10% contact limit, the model selected 300 customers and 212 of them churned. That produced 0.7067 precision and 0.3212 recall.

`days_since_last_purchase` was still the strongest feature by a large margin. The other strongest features were `avg_order_value`, `total_spend_90d`, `category_diversity_score`, and `purchase_frequency_30d`. These features describe the value, breadth, and recent strength of the customer's relationship with NorthStar. They added information that recency alone missed.

The weakest features by gain were `avg_basket_size_6m` at 9.64, `customer_tenure_days` at 9.92, and `purchase_frequency_90d` at 11.12. For comparison, recency had a gain of 53.01. I would not call the weaker features useless because they still appeared in the trees. However, they added much less signal than recency and the strongest spending features. This is an honest limit on what the extra feature engineering bought us.

## Customer service agent

The customer service design needs several steps. A request can require an order lookup, a policy lookup, a return or credit action, and sometimes an escalation. The correct next step depends on what earlier tools return. That makes an agent a better fit than a single fixed pipeline. A fixed workflow would be simpler for one narrow task, but it would become difficult to maintain across all of these different request paths.

The main production risk is an unauthorized action that loses money. A helpful-sounding wrong answer is bad, but an improper refund or repeated loyalty credit can directly cost the business money.

The $50 credit rule appears in three places for defense in depth. The system prompt tells the model what it should do before it chooses a tool. The validation in `tools.py` protects the application when the tool is called with a bad amount. The gateway policy makes the final decision before the Lambda tool runs. The layers cover different failure points. I would trust the gateway policy alone for the narrow per-request $50 limit because the model cannot talk it out of enforcing the rule. I would not trust any one layer for the safety of the whole agent. The gateway rule is also limited: it sees one request at a time, so two $40 credits can still pass separately.

## With more time and data

With ten times more time or data, I would improve the retention decision instead of only improving AUC. The current global top-10% threshold treats every churner as equally valuable. It also found only 2 of 21 Platinum churners even though the model ranked customers well within the Platinum tier. I would build a value-weighted targeting system using expected retained customer value, calibrated probabilities, and tier-aware allocations. I would then evaluate the actual value retained per contact, not only how many churners were found. This would connect the model more directly to NorthStar's business goal.
