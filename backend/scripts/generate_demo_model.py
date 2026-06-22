"""
Generate a pre-baked demo fallback model for the Model Action node.

Trains a tiny Decision Tree on the Iris dataset and saves it as a
scikit-learn Pipeline (StandardScaler -> DecisionTreeClassifier).

Run once during Docker build:
    python scripts/generate_demo_model.py
"""

import os
import joblib
from sklearn.datasets import load_iris
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

OUTPUT_DIR = os.environ.get("MODEL_STORAGE_PATH", "/app/storage/models")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    X, y = load_iris(return_X_y=True)
    target_names = load_iris().target_names.tolist()

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", DecisionTreeClassifier(max_depth=4, random_state=42)),
    ])
    pipeline.fit(X, y)

    output_path = os.path.join(OUTPUT_DIR, "demo_fallback.joblib")
    joblib.dump({
        "pipeline": pipeline,
        "target_names": target_names,
        "feature_names": ["sepal_length", "sepal_width", "petal_length", "petal_width"],
        "task_type": "classification",
        "description": "Demo Iris classifier (Decision Tree)",
    }, output_path)

    size_kb = os.path.getsize(output_path) / 1024
    print(f"✅ Demo model saved to {output_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
