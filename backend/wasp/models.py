"""Bounded regression model choices for the public WASP service."""

from __future__ import annotations

from typing import Any, Optional

from sklearn.linear_model import LinearRegression
from sklearn.neighbors import KNeighborsRegressor
from xgboost import XGBRegressor


MODEL_LABELS = {
    "linear": "Linear Regression",
    "knn": "K-Nearest Neighbors",
    "xgboost": "XGBoost",
}
SUPPORTED_MODELS = frozenset(MODEL_LABELS)


def validate_model(model: str) -> Optional[str]:
    """Return a user-facing validation error for unknown model identifiers."""
    if model not in SUPPORTED_MODELS:
        allowed = ", ".join(sorted(SUPPORTED_MODELS))
        return f"Unsupported model '{model}'. Choose one of: {allowed}."
    return None


def build_regressor(model: str):
    """Create a deterministic, resource-bounded regression estimator."""
    error = validate_model(model)
    if error:
        raise ValueError(error)
    if model == "linear":
        return LinearRegression()
    if model == "knn":
        return KNeighborsRegressor(
            n_neighbors=5,
            weights="distance",
            p=2,
            n_jobs=1,
        )
    return XGBRegressor(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        tree_method="hist",
        n_jobs=1,
        random_state=42,
        verbosity=0,
    )


def feature_attributions(
    fitted_model: Any,
    model: str,
    predictor_columns: list[str],
) -> dict[str, Any]:
    """Return only feature information intrinsic to the selected estimator."""
    if model == "linear":
        values = fitted_model.coef_
        kind = "coefficient"
    elif model == "xgboost":
        values = fitted_model.feature_importances_
        kind = "importance"
    else:
        return {"kind": "none", "items": []}

    items = [
        {"predictor": column, "value": round(float(value), 6)}
        for column, value in zip(predictor_columns, values)
    ]
    return {"kind": kind, "items": items}
