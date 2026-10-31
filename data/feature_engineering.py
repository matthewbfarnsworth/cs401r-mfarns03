from __future__ import annotations

import pandas as pd

N_CATEGORIES = 8.0
NO_PURCHASE_RECENCY = 999.0


def _history(transactions: pd.DataFrame, snapshot_date=None) -> pd.DataFrame:
    """Parse purchase_date to a whole day and drop rows after snapshot_date."""
    df = transactions.copy()
    df["purchase_date"] = pd.to_datetime(df["purchase_date"]).dt.normalize()
    if snapshot_date is not None:
        df = df[df["purchase_date"] <= pd.Timestamp(snapshot_date).normalize()]
    return df


def compute_recency_features(transactions, snapshot_date, customer_ids=None):
    T = pd.Timestamp(snapshot_date).normalize()
    history = _history(transactions, snapshot_date)
    dates = history.groupby("customer_id")["purchase_date"].agg(["max", "min"])
    features = pd.DataFrame({
        "customer_id": dates.index,
        "days_since_last_purchase": (T - dates["max"]).dt.days.astype(float).to_numpy(),
        "customer_tenure_days": (T - dates["min"]).dt.days.astype(float).to_numpy(),
    })
    if customer_ids is None:
        return features
    features = customer_ids[["customer_id"]].merge(features, on="customer_id", how="left")
    return features.fillna({
        "days_since_last_purchase": NO_PURCHASE_RECENCY,
        "customer_tenure_days": 0.0,
    })


def compute_frequency_features(transactions, snapshot_date):
    T = pd.Timestamp(snapshot_date).normalize()
    history = _history(transactions, snapshot_date)
    features = pd.DataFrame({"customer_id": history["customer_id"].unique()})
    for days in (30, 90, 180):
        in_window = history[history["purchase_date"] > T - pd.Timedelta(days=days)]
        counts = in_window.groupby("customer_id").size()
        features[f"purchase_frequency_{days}d"] = (
            features["customer_id"].map(counts).fillna(0.0).astype(float)
        )
    return features


def compute_monetary_features(transactions, snapshot_date):
    T = pd.Timestamp(snapshot_date).normalize()
    history = _history(transactions, snapshot_date)
    grouped = history.groupby("customer_id")
    features = pd.DataFrame({
        "avg_order_value": grouped["order_value"].mean(),
        "total_lifetime_value": grouped["order_value"].sum(),
    }).reset_index()

    last_90 = history[history["purchase_date"] > T - pd.Timedelta(days=90)]
    last_180 = history[history["purchase_date"] > T - pd.Timedelta(days=180)]
    features["total_spend_90d"] = (
        features["customer_id"]
        .map(last_90.groupby("customer_id")["order_value"].sum())
        .fillna(0.0)
    )
    # Mean items per order. No orders in 180 days gives 0, as in the Glue job.
    features["avg_basket_size_6m"] = (
        features["customer_id"]
        .map(last_180.groupby("customer_id")["num_items"].mean())
        .fillna(0.0)
    )
    return features


def compute_behavioral_features(transactions, snapshot_date=None):
    """Pass snapshot_date unless the rows are already limited to the history."""
    history = _history(transactions, snapshot_date)
    grouped = history.groupby("customer_id")
    categories = (
        history[history["product_category"] != "unknown"]
        .groupby("customer_id")["product_category"]
        .nunique()
    )
    online = history["channel"].eq("online").groupby(history["customer_id"]).mean()
    features = pd.DataFrame({"customer_id": list(grouped.groups)})
    features["category_diversity_score"] = (
        features["customer_id"].map(categories).fillna(0.0) / N_CATEGORIES
    )
    features["online_to_store_ratio"] = features["customer_id"].map(online).astype(float)
    return features


def build_churn_feature_set(customers, transactions, snapshot_date):
    """One row per roster customer. Customers with no purchases get defaults:
    recency 999, everything else 0, so no feature is ever null."""
    history = _history(transactions, snapshot_date)
    features = customers.merge(
        compute_recency_features(history, snapshot_date, customers[["customer_id"]]),
        on="customer_id", how="left",
    )
    for part in (
        compute_frequency_features(history, snapshot_date),
        compute_monetary_features(history, snapshot_date),
        compute_behavioral_features(history, snapshot_date),
    ):
        features = features.merge(part, on="customer_id", how="left")
    computed = [c for c in features.columns if c not in customers.columns]
    features[computed] = features[computed].fillna(0.0)
    return features