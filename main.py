from io import BytesIO
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError


ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


app = FastAPI(
    title="Image Quality Check API",
    description="Pre-screen uploaded images for data cleaning workflows.",
    version="1.0.0",
)


def _get_extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _load_image(file_bytes: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(file_bytes))
        image.load()
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="invalid_image_file") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="invalid_image_file") from exc

    return image.convert("RGB")


def _round_float(value: float) -> float:
    return round(float(value), 4)


def _laplacian_variance(gray: np.ndarray) -> float:
    gray_float = gray.astype(np.float64)
    padded = np.pad(gray_float, 1, mode="edge")
    laplacian = (
        padded[:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
        - 4 * padded[1:-1, 1:-1]
    )
    return float(laplacian.var())


def calculate_metrics(image: Image.Image) -> dict[str, int | float]:
    width, height = image.size
    short_side = min(width, height)

    gray = np.array(image.convert("L"))

    blur_score = _laplacian_variance(gray)
    brightness_mean = gray.mean()
    overexposed_ratio = np.mean(gray > 245)
    underexposed_ratio = np.mean(gray < 15)
    contrast_std = gray.std()

    return {
        "width": width,
        "height": height,
        "short_side": short_side,
        "blur_score": _round_float(blur_score),
        "brightness_mean": _round_float(brightness_mean),
        "overexposed_ratio": _round_float(overexposed_ratio),
        "underexposed_ratio": _round_float(underexposed_ratio),
        "contrast_std": _round_float(contrast_std),
    }


def judge_image(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if metrics["short_side"] < 512:
        reasons.append("resolution_low")
    if metrics["blur_score"] < 80:
        reasons.append("blur_or_low_quality")
    if metrics["brightness_mean"] < 35:
        reasons.append("too_dark")
    if metrics["brightness_mean"] > 220:
        reasons.append("too_bright")
    if metrics["overexposed_ratio"] > 0.35:
        reasons.append("overexposure")
    if metrics["underexposed_ratio"] > 0.45:
        reasons.append("underexposure")
    if metrics["contrast_std"] < 18:
        reasons.append("low_contrast")

    return bool(reasons), reasons


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/image-check")
async def image_check(file: UploadFile = File(...)) -> dict[str, Any]:
    extension = _get_extension(file.filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="unsupported_file_type")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")

    image = _load_image(file_bytes)
    metrics = calculate_metrics(image)
    script_skip, script_reasons = judge_image(metrics)

    return {
        "script_skip": script_skip,
        "script_reasons": script_reasons,
        "metrics": metrics,
    }
