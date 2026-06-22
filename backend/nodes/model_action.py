"""
Model Runner — Real ML/DL inference engine for the Model Action node.

Supports:
  - scikit-learn Pipelines (.pkl, .joblib)
  - ONNX Runtime (.onnx)
  - PyTorch (.pt, .pth) — guarded import
  - Keras/TF (.h5, .keras) — guarded import

Falls back to a pre-baked demo model (Iris Decision Tree) when no model
file is configured.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np

from core.expressions import resolve_template, apply_output_mapping

logger = logging.getLogger(__name__)

MODEL_STORAGE = os.getenv("MODEL_STORAGE_PATH", "/app/storage/models")
DEMO_MODEL_PATH = os.path.join(MODEL_STORAGE, "demo_fallback.joblib")

# ─── In-memory model cache ───────────────────────────────────────────────────
_model_cache: dict[str, Any] = {}


# ─── Public API ──────────────────────────────────────────────────────────────

async def execute_model_action(
    node_config: dict[str, Any],
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Load a trained model and run inference.

    Config keys:
      - model_name:     human-readable name
      - algorithm:      algorithm label (for display)
      - task_type:      classification | regression | clustering | anomaly_detection
      - features:       list of feature names for tabular data
      - model_path:     path to the model file
      - input_type:     features | text | image | json
      - preprocessing:  JSON string with DL preprocessing config
    """
    model_name = resolve_template(node_config.get("model_name", ""), input_data)
    algorithm = resolve_template(node_config.get("algorithm", "unknown"), input_data)
    task_type = resolve_template(node_config.get("task_type", "classification"), input_data)
    
    features_raw = node_config.get("features", [])
    features = [resolve_template(str(f), input_data) for f in features_raw] if isinstance(features_raw, list) else []
    
    model_path = resolve_template(node_config.get("model_path", ""), input_data)
    input_type = resolve_template(node_config.get("input_type", "features"), input_data)
    preprocessing_raw = resolve_template(node_config.get("preprocessing", "{}"), input_data)

    # ── Merge config-level inputs into input_data ──
    # Users can set text/image/json directly in the sidebar config
    if input_type == "text":
        config_text = resolve_template(node_config.get("text_input", ""), input_data)
        if config_text and not input_data.get("text") and not input_data.get("input"):
            input_data["text"] = config_text
    elif input_type == "image":
        config_image = resolve_template(node_config.get("image_path", ""), input_data)
        if config_image and not input_data.get("image_path") and not input_data.get("image"):
            input_data["image_path"] = config_image
    elif input_type == "json":
        config_json = resolve_template(node_config.get("json_input", ""), input_data)
        if config_json and not input_data.get("data"):
            try:
                input_data["data"] = json.loads(config_json) if isinstance(config_json, str) else config_json
            except (json.JSONDecodeError, TypeError):
                pass

    # ── Validate config ──
    validation_errors = []
    if not model_name or model_name.strip() == "":
        validation_errors.append("Model Name is required. Enter a name in the sidebar config.")
    if algorithm in ("unknown", ""):
        validation_errors.append("Algorithm is required. Select an algorithm in the sidebar config.")

    # Validate input data based on input_type
    if input_type == "text":
        text_val = input_data.get("text", input_data.get("input", ""))
        if not text_val:
            validation_errors.append(
                "Text input is empty. Provide 'text' in the input data, "
                "or connect to an upstream node that outputs text."
            )
    elif input_type == "image":
        image_val = input_data.get("image_path", input_data.get("image", ""))
        if not image_val:
            validation_errors.append(
                "Image path is empty. Provide 'image_path' in the input data, "
                "set the image path in the node config, "
                "or connect to an upstream node that outputs an image path."
            )
    elif input_type == "features":
        if not features and not model_path:
            validation_errors.append(
                "No features specified and no model uploaded. "
                "Add feature names (e.g., sepal_length, sepal_width) or upload a model."
            )

    if validation_errors:
        return {
            "error": "Configuration incomplete:\n• " + "\n• ".join(validation_errors),
            "__input_error__": True
        }

    # Parse preprocessing config
    try:
        preprocessing = json.loads(preprocessing_raw) if isinstance(preprocessing_raw, str) else preprocessing_raw
    except (json.JSONDecodeError, TypeError):
        preprocessing = {}

    # ── Determine which model to load ──
    use_demo = False
    if not model_path or not os.path.exists(model_path):
        if os.path.exists(DEMO_MODEL_PATH):
            # Demo only supports features input type (Iris classifier)
            if input_type != "features":
                return {
                    "error": f"No model file uploaded. The demo fallback only supports 'features' input type, "
                             f"but you selected '{input_type}'. "
                             f"Upload a model file (.pkl, .joblib, .onnx, .h5, .pt) for '{input_type}' inputs.",
                    "model_name": model_name,
                    "status": "error",
                }
            model_path = DEMO_MODEL_PATH
            use_demo = True
            logger.info("No model configured — using demo fallback (Iris Decision Tree)")
        else:
            return {
                "error": "No model file configured and demo fallback not found. "
                         "Upload a model file (.pkl, .joblib, .onnx) in the sidebar.",
                "__input_error__": True
            }

    # ── Load the model ──
    try:
        model_obj = await asyncio.to_thread(_load_model, model_path)
    except EnvironmentError as e:
        return {
            "error": str(e),
            "model_name": model_name,
            "status": "error",
        }
    except Exception as e:
        logger.error(f"Failed to load model '{model_path}': {e}")
        return {
            "error": f"Failed to load model: {e}",
            "__input_error__": True
        }

    # ── Prepare input ──
    try:
        prepared_input = _prepare_input(
            model_obj, input_data, input_type, features, preprocessing, use_demo
        )
    except ValueError as e:
        return {
            "error": str(e),
            "model_name": model_name,
            "status": "error",
        }

    # ── Run inference ──
    try:
        result = await asyncio.to_thread(
            _run_inference, model_obj, prepared_input, task_type
        )
    except Exception as e:
        logger.error(f"Inference failed: {e}")
        return {
            "error": f"Inference failed: {e}",
            "model_name": model_name,
            "status": "error",
        }

    # ── Build output ──
    output = {
        "model_name": model_name,
        "algorithm": algorithm,
        "task_type": task_type,
        "input_type": input_type,
        "status": "success",
        **result,
    }
    if use_demo:
        output["note"] = "Used demo fallback model (Iris Decision Tree). Upload your own model for real predictions."

    return output


# ─── Model Loading ───────────────────────────────────────────────────────────

def _load_model(model_path: str) -> dict[str, Any]:
    """Load a model from disk with caching. Returns a dict with 'type' and model object."""
    if model_path in _model_cache:
        return _model_cache[model_path]

    ext = Path(model_path).suffix.lower()

    if ext in (".pkl", ".joblib"):
        model_obj = _load_sklearn(model_path)
    elif ext == ".onnx":
        model_obj = _load_onnx(model_path)
    elif ext in (".h5", ".keras"):
        model_obj = _load_keras(model_path)
    elif ext in (".pt", ".pth"):
        model_obj = _load_pytorch(model_path)
    else:
        raise ValueError(
            f"Unsupported model format: '{ext}'. "
            f"Supported: .pkl, .joblib, .onnx, .h5, .keras, .pt, .pth"
        )

    _model_cache[model_path] = model_obj
    return model_obj


def _load_sklearn(path: str) -> dict[str, Any]:
    """Load a scikit-learn Pipeline or model."""
    import joblib

    data = joblib.load(path)

    # Support our bundled format (dict with 'pipeline' key)
    if isinstance(data, dict) and "pipeline" in data:
        return {
            "type": "sklearn",
            "model": data["pipeline"],
            "target_names": data.get("target_names"),
            "feature_names": data.get("feature_names"),
            "meta": {k: v for k, v in data.items() if k not in ("pipeline",)},
        }

    # Raw sklearn estimator or pipeline
    return {
        "type": "sklearn",
        "model": data,
        "target_names": None,
        "feature_names": None,
        "meta": {},
    }


def _load_onnx(path: str) -> dict[str, Any]:
    """Load an ONNX model via onnxruntime."""
    import onnxruntime as ort

    session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    input_info = session.get_inputs()
    output_info = session.get_outputs()

    return {
        "type": "onnx",
        "model": session,
        "input_names": [i.name for i in input_info],
        "input_shapes": [i.shape for i in input_info],
        "output_names": [o.name for o in output_info],
        "meta": {},
    }


def _load_keras(path: str) -> dict[str, Any]:
    """Load a Keras model — guarded import."""
    try:
        import tensorflow as tf
    except ImportError:
        raise EnvironmentError(
            "TensorFlow/Keras is not installed in this environment. "
            "Please convert your model to .onnx format and re-upload, "
            "or rebuild the Docker image with TensorFlow included.\n\n"
            "To convert: python -m tf2onnx.convert --saved-model ./model --output model.onnx"
        )

    model = tf.keras.models.load_model(path)
    return {
        "type": "keras",
        "model": model,
        "input_shape": model.input_shape,
        "output_shape": model.output_shape,
        "meta": {},
    }


def _load_pytorch(path: str) -> dict[str, Any]:
    """Load a PyTorch model — guarded import."""
    try:
        import torch
    except ImportError:
        raise EnvironmentError(
            "PyTorch is not installed in this environment. "
            "Please convert your model to .onnx format and re-upload, "
            "or rebuild the Docker image with PyTorch included.\n\n"
            "To convert:\n"
            "  import torch\n"
            "  model = torch.load('model.pt')\n"
            "  dummy = torch.randn(1, input_size)\n"
            "  torch.onnx.export(model, dummy, 'model.onnx')"
        )

    model = torch.load(path, map_location="cpu", weights_only=False)
    if hasattr(model, "eval"):
        model.eval()

    return {
        "type": "pytorch",
        "model": model,
        "meta": {},
    }


# ─── Input Preparation ──────────────────────────────────────────────────────

def _prepare_input(
    model_obj: dict[str, Any],
    input_data: dict[str, Any],
    input_type: str,
    features: list[str],
    preprocessing: dict,
    use_demo: bool,
) -> np.ndarray:
    """Prepare input data for inference based on input_type."""

    if input_type == "features":
        return _prepare_features(model_obj, input_data, features, use_demo)
    elif input_type == "text":
        return _prepare_text(input_data, preprocessing)
    elif input_type == "image":
        return _prepare_image(input_data, preprocessing)
    elif input_type == "json":
        return _prepare_json(input_data)
    else:
        raise ValueError(f"Unknown input_type: '{input_type}'. Use: features, text, image, json")


def _prepare_features(
    model_obj: dict[str, Any],
    input_data: dict[str, Any],
    features: list[str],
    use_demo: bool,
) -> np.ndarray:
    """Build a feature vector from input_data."""

    # For demo model with no user features, use the built-in feature names
    if use_demo and not features:
        features = model_obj.get("feature_names") or []
        if not features:
            # Provide sensible demo defaults for Iris
            features = ["sepal_length", "sepal_width", "petal_length", "petal_width"]

    if not features:
        raise ValueError(
            "No features specified. Add feature names in the node configuration, "
            "e.g. ['age', 'income', 'score']."
        )

    warnings = []
    values = []
    for feat in features:
        val = input_data.get(feat)
        if val is None:
            warnings.append(f"Missing feature '{feat}', defaulting to 0.0")
            val = 0.0
        try:
            values.append(float(val))
        except (ValueError, TypeError):
            warnings.append(f"Non-numeric feature '{feat}' = '{val}', defaulting to 0.0")
            values.append(0.0)

    if warnings:
        logger.warning(f"Feature preparation warnings: {warnings}")

    return np.array([values], dtype=np.float32)


def _prepare_text(input_data: dict[str, Any], preprocessing: dict) -> Any:
    """Prepare text input. The sklearn Pipeline should include a vectorizer."""
    text = input_data.get("text", input_data.get("input", ""))
    if not text:
        raise ValueError("No text input found. Provide 'text' or 'input' in the input data.")
    # Return as list of strings — sklearn expects this shape for text pipelines
    return [str(text)]


def _prepare_image(input_data: dict[str, Any], preprocessing: dict) -> np.ndarray:
    """Prepare image input with configurable preprocessing."""
    image_path = input_data.get("image_path", input_data.get("image", ""))

    if not image_path:
        raise ValueError(
            "No image input found. Provide 'image_path' in the input data."
        )

    # Read image as numpy array
    if not os.path.exists(image_path):
        raise ValueError(f"Image file not found: {image_path}")

    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
    except ImportError:
        raise EnvironmentError(
            "Pillow is not installed. Add 'Pillow' to requirements.txt for image support."
        )

    # Apply preprocessing from JSON config
    target_size = preprocessing.get("target_size", [224, 224])
    img = img.resize(tuple(target_size))
    img_array = np.array(img, dtype=np.float32)

    if preprocessing.get("normalize", True):
        img_array = img_array / 255.0

    if preprocessing.get("channels_first", False):
        # HWC -> CHW
        img_array = np.transpose(img_array, (2, 0, 1))

    # Add batch dimension
    img_array = np.expand_dims(img_array, axis=0)
    return img_array


def _prepare_json(input_data: dict[str, Any]) -> Any:
    """Pass structured JSON data directly."""
    data = input_data.get("data", input_data)
    if isinstance(data, dict):
        # Try to convert to flat array
        values = list(data.values())
        try:
            return np.array([values], dtype=np.float32)
        except (ValueError, TypeError):
            return data
    return data


# ─── Inference ───────────────────────────────────────────────────────────────

def _run_inference(
    model_obj: dict[str, Any],
    prepared_input: Any,
    task_type: str,
) -> dict[str, Any]:
    """Run inference and format the result based on task_type."""

    model_type = model_obj["type"]

    if model_type == "sklearn":
        return _infer_sklearn(model_obj, prepared_input, task_type)
    elif model_type == "onnx":
        return _infer_onnx(model_obj, prepared_input, task_type)
    elif model_type == "keras":
        return _infer_keras(model_obj, prepared_input, task_type)
    elif model_type == "pytorch":
        return _infer_pytorch(model_obj, prepared_input, task_type)
    else:
        raise ValueError(f"Unknown model type: {model_type}")


def _infer_sklearn(model_obj: dict, prepared_input: Any, task_type: str) -> dict:
    """Run inference on a scikit-learn Pipeline/model."""
    model = model_obj["model"]
    target_names = model_obj.get("target_names")

    if task_type in ("classification", "regression"):
        prediction_raw = model.predict(prepared_input)
        prediction = prediction_raw[0]

        result: dict[str, Any] = {}

        if task_type == "classification":
            # Map to target name if available
            if target_names and isinstance(prediction, (int, np.integer)):
                idx = int(prediction)
                prediction_label = target_names[idx] if idx < len(target_names) else str(prediction)
            else:
                prediction_label = str(prediction)

            result["prediction"] = prediction_label

            # Try to get probabilities
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(prepared_input)[0]
                if target_names and len(target_names) == len(proba):
                    result["probabilities"] = {
                        name: round(float(p), 4) for name, p in zip(target_names, proba)
                    }
                else:
                    result["probabilities"] = {
                        f"class_{i}": round(float(p), 4) for i, p in enumerate(proba)
                    }
                result["confidence"] = round(float(max(proba)), 4)
            else:
                result["confidence"] = 1.0

        else:  # regression
            result["prediction"] = round(float(prediction), 6)

        return result

    elif task_type == "clustering":
        if hasattr(model, "predict"):
            cluster_id = int(model.predict(prepared_input)[0])
        elif hasattr(model, "fit_predict"):
            cluster_id = int(model.fit_predict(prepared_input)[0])
        else:
            raise ValueError("Model does not support clustering prediction.")

        result = {"cluster_id": cluster_id}

        if hasattr(model, "transform"):
            distances = model.transform(prepared_input)[0]
            result["distances"] = [round(float(d), 4) for d in distances]

        return result

    elif task_type == "anomaly_detection":
        if hasattr(model, "predict"):
            pred = model.predict(prepared_input)[0]
            is_anomaly = int(pred) == -1
        elif hasattr(model, "decision_function"):
            score = float(model.decision_function(prepared_input)[0])
            is_anomaly = score < 0
        else:
            raise ValueError("Model does not support anomaly detection.")

        result = {"is_anomaly": is_anomaly}
        if hasattr(model, "decision_function"):
            result["anomaly_score"] = round(float(model.decision_function(prepared_input)[0]), 4)
        if hasattr(model, "score_samples"):
            result["anomaly_score"] = round(float(model.score_samples(prepared_input)[0]), 4)

        return result

    raise ValueError(f"Unsupported task_type: '{task_type}'")


def _infer_onnx(model_obj: dict, prepared_input: np.ndarray, task_type: str) -> dict:
    """Run ONNX inference."""
    session = model_obj["model"]
    input_name = model_obj["input_names"][0]

    if not isinstance(prepared_input, np.ndarray):
        prepared_input = np.array(prepared_input, dtype=np.float32)

    outputs = session.run(None, {input_name: prepared_input})
    raw_output = outputs[0][0]  # First output, first batch item

    if task_type == "classification":
        if len(raw_output.shape) == 0:
            return {"prediction": str(raw_output), "confidence": 1.0}

        predicted_idx = int(np.argmax(raw_output))
        proba = np.exp(raw_output) / np.sum(np.exp(raw_output))  # softmax
        confidence = round(float(proba[predicted_idx]), 4)

        return {
            "prediction": f"class_{predicted_idx}",
            "confidence": confidence,
            "probabilities": {f"class_{i}": round(float(p), 4) for i, p in enumerate(proba)},
        }
    elif task_type == "regression":
        val = float(raw_output) if raw_output.shape == () else float(raw_output[0])
        return {"prediction": round(val, 6)}
    else:
        return {"raw_output": raw_output.tolist()}


def _infer_keras(model_obj: dict, prepared_input: np.ndarray, task_type: str) -> dict:
    """Run Keras/TF inference."""
    model = model_obj["model"]
    output = model.predict(prepared_input, verbose=0)
    raw_output = output[0]

    if task_type == "classification":
        predicted_idx = int(np.argmax(raw_output))
        confidence = round(float(raw_output[predicted_idx]), 4)
        return {
            "prediction": f"class_{predicted_idx}",
            "confidence": confidence,
            "probabilities": {f"class_{i}": round(float(p), 4) for i, p in enumerate(raw_output)},
        }
    elif task_type == "regression":
        return {"prediction": round(float(raw_output[0]), 6)}
    else:
        return {"raw_output": raw_output.tolist()}


def _infer_pytorch(model_obj: dict, prepared_input: np.ndarray, task_type: str) -> dict:
    """Run PyTorch inference."""
    import torch

    model = model_obj["model"]
    tensor = torch.tensor(prepared_input, dtype=torch.float32)

    with torch.no_grad():
        output = model(tensor)

    raw_output = output[0].numpy() if hasattr(output, "numpy") else output[0].detach().numpy()

    if task_type == "classification":
        proba = np.exp(raw_output) / np.sum(np.exp(raw_output))
        predicted_idx = int(np.argmax(proba))
        confidence = round(float(proba[predicted_idx]), 4)
        return {
            "prediction": f"class_{predicted_idx}",
            "confidence": confidence,
            "probabilities": {f"class_{i}": round(float(p), 4) for i, p in enumerate(proba)},
        }
    elif task_type == "regression":
        return {"prediction": round(float(raw_output[0]), 6)}
    else:
        return {"raw_output": raw_output.tolist()}


# ─── Model Listing ──────────────────────────────────────────────────────────

async def list_available_models() -> list[dict[str, str]]:
    """List model files in the storage directory."""
    models = []
    if os.path.exists(MODEL_STORAGE):
        for fname in os.listdir(MODEL_STORAGE):
            if fname.endswith((".pkl", ".h5", ".keras", ".joblib", ".onnx", ".pt", ".pth")):
                models.append({
                    "name": fname.rsplit(".", 1)[0],
                    "file": fname,
                    "format": fname.rsplit(".", 1)[1],
                    "path": os.path.join(MODEL_STORAGE, fname),
                })
    return models

from typing import Any
from core.node_registry import register_action

@register_action(
    type="model_action",
    label="ML Model",
    icon="🧠",
    description="Run prediction/classification using a trained ML or DL model",
    category="Model Actions",
    config_schema={
        "model_name": {
            "type": "string",
            "label": "Model Name",
            "required": True,
            "placeholder": "My Classifier",
            "description": "A human-readable name for this model.",
        },
        "algorithm": {
            "type": "select",
            "label": "Algorithm",
            "default": "RandomForest",
            "options": [
                "RandomForest", "DecisionTree", "SVM", "LogisticRegression",
                "KNN", "NaiveBayes", "GradientBoosting", "XGBoost",
                "NeuralNetwork", "Custom",
            ],
            "description": "The ML algorithm used by the model.",
        },
        "task_type": {
            "type": "select",
            "label": "Task Type",
            "default": "classification",
            "options": [
                {"value": "classification", "label": "Classification"},
                {"value": "regression", "label": "Regression"},
                {"value": "clustering", "label": "Clustering"},
                {"value": "anomaly_detection", "label": "Anomaly Detection"},
            ],
            "description": "The type of ML task.",
        },
        "input_type": {
            "type": "select",
            "label": "Input Type",
            "default": "features",
            "options": [
                {"value": "features", "label": "Feature Vector"},
                {"value": "text", "label": "Text"},
                {"value": "image", "label": "Image"},
                {"value": "json", "label": "Raw JSON"},
            ],
            "description": "What kind of input data the model expects.",
        },
        "features": {
            "type": "string",
            "label": "Feature Names",
            "multiline": True,
            "placeholder": "sepal_length, sepal_width, petal_length, petal_width",
            "description": "Comma-separated list of feature names for tabular input.",
            "visibleWhen": {"input_type": "features"},
        },
        "model_path": {
            "type": "file",
            "label": "Model File",
            "description": "Upload a model file (.pkl, .joblib, .onnx, .h5, .pt).",
        },
        "preprocessing": {
            "type": "code",
            "label": "Preprocessing Config",
            "default": "{}",
            "description": "JSON preprocessing config (target_size, normalize, channels_first).",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "response"}',
        },
    },
)
async def handle_model_action(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    res = await execute_model_action(config, input_data)
    return apply_output_mapping(res, config.get("output_mapping", ""))
