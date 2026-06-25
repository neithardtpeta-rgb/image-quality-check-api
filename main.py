from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from starlette.datastructures import UploadFile


ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_IMAGE_FORMATS = {"jpeg", "png", "webp"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_URL_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_URL_TIMEOUT_SECONDS = 10.0


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

    image_format = (image.format or "").lower()
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(status_code=400, detail="unsupported_image_format")

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


async def _read_uploaded_file(file: UploadFile) -> bytes:
    extension = _get_extension(file.filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="unsupported_file_type")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")

    return file_bytes


def _validate_image_url(image_url: str) -> str:
    cleaned_url = image_url.strip()
    parsed = urlparse(cleaned_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid_image_url")

    return cleaned_url


async def _download_image_url(image_url: str) -> bytes:
    cleaned_url = _validate_image_url(image_url)
    timeout = httpx.Timeout(IMAGE_URL_TIMEOUT_SECONDS)
    headers = {"User-Agent": "image-quality-check-api/1.0"}

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", cleaned_url, headers=headers) as response:
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "image_url_download_failed",
                            "status_code": response.status_code,
                        },
                    )

                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if declared_size > MAX_URL_IMAGE_BYTES:
                        raise HTTPException(status_code=413, detail="image_url_file_too_large")

                chunks: list[bytes] = []
                total_size = 0
                async for chunk in response.aiter_bytes():
                    total_size += len(chunk)
                    if total_size > MAX_URL_IMAGE_BYTES:
                        raise HTTPException(status_code=413, detail="image_url_file_too_large")
                    chunks.append(chunk)

    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="image_url_download_timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="image_url_download_failed") from exc

    if not chunks:
        raise HTTPException(status_code=400, detail="image_url_empty_response")

    return b"".join(chunks)


async def _extract_image_input(request: Request) -> tuple[UploadFile | None, str | None]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    file: UploadFile | None = None
    image_url: str | None = None

    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_json_body") from exc

        if isinstance(payload, dict):
            raw_image_url = payload.get("image_url")
            if isinstance(raw_image_url, str):
                image_url = raw_image_url
    else:
        form = await request.form()
        raw_file = form.get("file")
        raw_image_url = form.get("image_url")

        if isinstance(raw_file, UploadFile) and raw_file.filename:
            file = raw_file
        if isinstance(raw_image_url, str):
            image_url = raw_image_url

    return file, image_url


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/image-check")
async def image_check(request: Request) -> dict[str, Any]:
    file, image_url = await _extract_image_input(request)

    if file is not None:
        file_bytes = await _read_uploaded_file(file)
    elif image_url:
        file_bytes = await _download_image_url(image_url)
    else:
        raise HTTPException(status_code=400, detail="missing_file_or_image_url")

    image = _load_image(file_bytes)
    metrics = calculate_metrics(image)
    script_skip, script_reasons = judge_image(metrics)

    return {
        "script_skip": script_skip,
        "script_reasons": script_reasons,
        "metrics": metrics,
    }
