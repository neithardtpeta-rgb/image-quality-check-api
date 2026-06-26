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

CENTER_CROP_RATIO = 0.6
RESIZED_BLUR_MAX_SIDE = 512
HEURISTIC_ANALYSIS_MAX_SIDE = 512

MIN_SHORT_SIDE = 384
MIN_IMAGE_AREA = 200_000
MAX_ASPECT_RATIO = 4.0
MIN_ASPECT_RATIO = 0.25

OVEREXPOSED_BRIGHTNESS_MIN = 185.0
OVEREXPOSED_RATIO_MIN = 0.08
WASHED_OUT_BRIGHTNESS_MIN = 190.0
WASHED_OUT_CONTRAST_MAX = 55.0
SEVERE_WASHED_OUT_BRIGHTNESS_MIN = 200.0
SEVERE_WASHED_OUT_CONTRAST_MAX = 65.0
UNDEREXPOSED_BRIGHTNESS_MAX = 45.0
UNDEREXPOSED_RATIO_MIN = 0.35
LOW_CONTRAST_STD_MAX = 25.0

CENTER_BLUR_SCORE_MIN = 80.0
CENTER_CONTRAST_STD_MIN = 35.0
RESIZED_BLUR_SCORE_MIN = 60.0
FALSE_SHARPNESS_BLUR_SCORE_MIN = 80.0

EDGE_GRADIENT_THRESHOLD = 12.0
HIGH_FREQUENCY_LAPLACIAN_THRESHOLD = 35.0
SMALL_COMPONENT_MIN_AREA = 2
SMALL_COMPONENT_MAX_AREA = 80
DENSE_SMALL_COMPONENT_COUNT_MIN = 500
VERY_DENSE_SMALL_COMPONENT_COUNT_MIN = 800
DENSE_EDGE_DENSITY_MIN = 0.08
DENSE_WATERMARK_BRIGHTNESS_MIN = 170.0
OVERLAY_TEXTURE_CONTRAST_MAX = 55.0
OVERLAY_HIGH_FREQUENCY_DENSITY_MIN = 0.08

STRAIGHT_LINE_ROW_COL_DENSITY_MIN = 0.45
UNIFORM_BLOCK_GRID_SIZE = 8
UNIFORM_BLOCK_STD_MAX = 8.0
COLLAGE_STRAIGHT_LINE_COUNT_MIN = 22
COLLAGE_GRID_LINE_SCORE_MIN = 0.022
COLLAGE_EDGE_DENSITY_MIN = 0.05
UI_STRAIGHT_LINE_COUNT_MIN = 30
UI_UNIFORM_BLOCKS_RATIO_MIN = 0.55
UI_EDGE_DENSITY_MIN = 0.035


app = FastAPI(
    title="Image Quality Check API",
    description="Pre-screen uploaded images for data cleaning workflows.",
    version="1.0.0",
)


class ImageUnavailableError(Exception):
    pass


def _get_extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _load_image(file_bytes: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(file_bytes))
        image.load()
    except UnidentifiedImageError as exc:
        raise ImageUnavailableError from exc
    except OSError as exc:
        raise ImageUnavailableError from exc

    image_format = (image.format or "").lower()
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ImageUnavailableError

    return image.convert("RGB")


def _round_float(value: float) -> float:
    return round(float(value), 4)


def _laplacian_array(gray: np.ndarray) -> np.ndarray:
    gray_float = gray.astype(np.float64)
    padded = np.pad(gray_float, 1, mode="edge")
    return (
        padded[:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
        - 4 * padded[1:-1, 1:-1]
    )


def _laplacian_variance(gray: np.ndarray) -> float:
    laplacian = _laplacian_array(gray)
    return float(laplacian.var())


def _resize_gray(gray: np.ndarray, max_side: int) -> np.ndarray:
    height, width = gray.shape
    longest_side = max(width, height)
    if longest_side <= max_side:
        return gray

    scale = max_side / longest_side
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    resized = Image.fromarray(gray).resize(
        (resized_width, resized_height),
        Image.Resampling.BILINEAR,
    )
    return np.array(resized)


def _center_crop(gray: np.ndarray, ratio: float) -> np.ndarray:
    height, width = gray.shape
    crop_width = max(1, int(round(width * ratio)))
    crop_height = max(1, int(round(height * ratio)))
    left = max(0, (width - crop_width) // 2)
    top = max(0, (height - crop_height) // 2)
    return gray[top : top + crop_height, left : left + crop_width]


def _gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    gray_float = gray.astype(np.float64)
    grad_y, grad_x = np.gradient(gray_float)
    return np.hypot(grad_x, grad_y)


def _count_small_components(mask: np.ndarray) -> int:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    count = 0

    for start_y, start_x in zip(*np.nonzero(mask)):
        if visited[start_y, start_x]:
            continue

        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        area = 0

        while stack:
            y, x = stack.pop()
            area += 1

            for next_y, next_x in (
                (y - 1, x),
                (y + 1, x),
                (y, x - 1),
                (y, x + 1),
            ):
                if (
                    0 <= next_y < height
                    and 0 <= next_x < width
                    and mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    stack.append((next_y, next_x))

        if SMALL_COMPONENT_MIN_AREA <= area <= SMALL_COMPONENT_MAX_AREA:
            count += 1

    return count


def _uniform_blocks_ratio(gray: np.ndarray) -> float:
    height, width = gray.shape
    block_height = max(1, height // UNIFORM_BLOCK_GRID_SIZE)
    block_width = max(1, width // UNIFORM_BLOCK_GRID_SIZE)
    total_blocks = 0
    uniform_blocks = 0

    for top in range(0, height, block_height):
        for left in range(0, width, block_width):
            block = gray[top : top + block_height, left : left + block_width]
            if block.size == 0:
                continue

            total_blocks += 1
            if float(block.std()) < UNIFORM_BLOCK_STD_MAX:
                uniform_blocks += 1

    if total_blocks == 0:
        return 0.0

    return uniform_blocks / total_blocks


def _line_metrics(edge_mask: np.ndarray) -> dict[str, int | float]:
    height, width = edge_mask.shape
    if height == 0 or width == 0:
        return {
            "straight_line_count": 0,
            "grid_line_score": 0.0,
            "border_line_density": 0.0,
        }

    row_density = edge_mask.mean(axis=1)
    column_density = edge_mask.mean(axis=0)
    horizontal_line_count = int(np.sum(row_density >= STRAIGHT_LINE_ROW_COL_DENSITY_MIN))
    vertical_line_count = int(np.sum(column_density >= STRAIGHT_LINE_ROW_COL_DENSITY_MIN))
    straight_line_count = horizontal_line_count + vertical_line_count
    grid_line_score = straight_line_count / (height + width)

    border_size = max(1, int(round(min(height, width) * 0.05)))
    border_mask = np.zeros_like(edge_mask, dtype=bool)
    border_mask[:border_size, :] = True
    border_mask[-border_size:, :] = True
    border_mask[:, :border_size] = True
    border_mask[:, -border_size:] = True
    border_line_density = float(edge_mask[border_mask].mean())

    return {
        "straight_line_count": straight_line_count,
        "grid_line_score": grid_line_score,
        "border_line_density": border_line_density,
    }


def _unavailable_metrics() -> dict[str, int | float]:
    return {
        "width": 0,
        "height": 0,
        "short_side": 0,
        "blur_score": 0.0,
        "center_blur_score": 0.0,
        "resized_blur_score": 0.0,
        "brightness_mean": 0.0,
        "overexposed_ratio": 0.0,
        "underexposed_ratio": 0.0,
        "contrast_std": 0.0,
        "center_contrast_std": 0.0,
        "edge_density": 0.0,
        "small_component_count": 0,
        "high_frequency_density": 0.0,
        "aspect_ratio": 0.0,
        "image_area": 0,
        "border_line_density": 0.0,
        "straight_line_count": 0,
        "grid_line_score": 0.0,
        "large_uniform_blocks_ratio": 0.0,
    }


def _skip_response(reason: str) -> dict[str, Any]:
    return {
        "script_skip": True,
        "script_reasons": [reason],
        "metrics": _unavailable_metrics(),
    }


def calculate_metrics(image: Image.Image) -> dict[str, int | float]:
    width, height = image.size
    short_side = min(width, height)
    image_area = width * height
    aspect_ratio = width / height if height else 0.0

    gray = np.array(image.convert("L"))
    center_gray = _center_crop(gray, CENTER_CROP_RATIO)
    resized_gray = _resize_gray(gray, RESIZED_BLUR_MAX_SIDE)
    analysis_gray = _resize_gray(gray, HEURISTIC_ANALYSIS_MAX_SIDE)

    blur_score = _laplacian_variance(gray)
    center_blur_score = _laplacian_variance(center_gray)
    resized_blur_score = _laplacian_variance(resized_gray)
    brightness_mean = gray.mean()
    overexposed_ratio = np.mean(gray > 245)
    underexposed_ratio = np.mean(gray < 15)
    contrast_std = gray.std()
    center_contrast_std = center_gray.std()

    gradient = _gradient_magnitude(analysis_gray)
    edge_mask = gradient > EDGE_GRADIENT_THRESHOLD
    edge_density = float(edge_mask.mean())

    laplacian_abs = np.abs(_laplacian_array(analysis_gray))
    high_frequency_mask = laplacian_abs > HIGH_FREQUENCY_LAPLACIAN_THRESHOLD
    high_frequency_density = float(high_frequency_mask.mean())
    small_component_count = _count_small_components(high_frequency_mask)

    line_metrics = _line_metrics(edge_mask)
    large_uniform_blocks_ratio = _uniform_blocks_ratio(analysis_gray)

    return {
        "width": width,
        "height": height,
        "short_side": short_side,
        "blur_score": _round_float(blur_score),
        "center_blur_score": _round_float(center_blur_score),
        "resized_blur_score": _round_float(resized_blur_score),
        "brightness_mean": _round_float(brightness_mean),
        "overexposed_ratio": _round_float(overexposed_ratio),
        "underexposed_ratio": _round_float(underexposed_ratio),
        "contrast_std": _round_float(contrast_std),
        "center_contrast_std": _round_float(center_contrast_std),
        "edge_density": _round_float(edge_density),
        "small_component_count": small_component_count,
        "high_frequency_density": _round_float(high_frequency_density),
        "aspect_ratio": _round_float(aspect_ratio),
        "image_area": image_area,
        "border_line_density": _round_float(float(line_metrics["border_line_density"])),
        "straight_line_count": int(line_metrics["straight_line_count"]),
        "grid_line_score": _round_float(float(line_metrics["grid_line_score"])),
        "large_uniform_blocks_ratio": _round_float(large_uniform_blocks_ratio),
    }


def judge_image(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if metrics["short_side"] < MIN_SHORT_SIDE:
        add_reason("resolution_too_small")
    if metrics["image_area"] < MIN_IMAGE_AREA:
        add_reason("image_area_too_small")
    if (
        metrics["aspect_ratio"] > MAX_ASPECT_RATIO
        or metrics["aspect_ratio"] < MIN_ASPECT_RATIO
    ):
        add_reason("extreme_aspect_ratio")

    if (
        metrics["brightness_mean"] >= OVEREXPOSED_BRIGHTNESS_MIN
        and metrics["overexposed_ratio"] >= OVEREXPOSED_RATIO_MIN
    ):
        add_reason("overexposed_or_washed_out")
    if (
        metrics["brightness_mean"] >= WASHED_OUT_BRIGHTNESS_MIN
        and metrics["contrast_std"] < WASHED_OUT_CONTRAST_MAX
    ):
        add_reason("washed_out_low_contrast")
    if (
        metrics["brightness_mean"] >= SEVERE_WASHED_OUT_BRIGHTNESS_MIN
        and metrics["contrast_std"] < SEVERE_WASHED_OUT_CONTRAST_MAX
    ):
        add_reason("severely_washed_out")
    if (
        metrics["brightness_mean"] <= UNDEREXPOSED_BRIGHTNESS_MAX
        and metrics["underexposed_ratio"] >= UNDEREXPOSED_RATIO_MIN
    ):
        add_reason("underexposed_too_dark")
    if metrics["contrast_std"] < LOW_CONTRAST_STD_MAX:
        add_reason("low_contrast")

    if (
        metrics["center_blur_score"] < CENTER_BLUR_SCORE_MIN
        and metrics["center_contrast_std"] < CENTER_CONTRAST_STD_MIN
    ):
        add_reason("center_subject_blurry")
    if metrics["resized_blur_score"] < RESIZED_BLUR_SCORE_MIN:
        add_reason("global_blurry_after_resize")
    if (
        metrics["blur_score"] >= FALSE_SHARPNESS_BLUR_SCORE_MIN
        and metrics["brightness_mean"] >= OVEREXPOSED_BRIGHTNESS_MIN
        and metrics["contrast_std"] < WASHED_OUT_CONTRAST_MAX
    ):
        add_reason("false_sharpness_from_overlay_or_texture")

    if (
        metrics["small_component_count"] >= DENSE_SMALL_COMPONENT_COUNT_MIN
        and metrics["edge_density"] > DENSE_EDGE_DENSITY_MIN
        and metrics["brightness_mean"] >= DENSE_WATERMARK_BRIGHTNESS_MIN
    ):
        add_reason("dense_watermark_or_text_overlay")
    if (
        metrics["small_component_count"] >= VERY_DENSE_SMALL_COMPONENT_COUNT_MIN
        and metrics["contrast_std"] < OVERLAY_TEXTURE_CONTRAST_MAX
        and metrics["high_frequency_density"] >= OVERLAY_HIGH_FREQUENCY_DENSITY_MIN
    ):
        add_reason("overlay_texture_affects_subject")

    if (
        metrics["straight_line_count"] >= COLLAGE_STRAIGHT_LINE_COUNT_MIN
        and metrics["grid_line_score"] >= COLLAGE_GRID_LINE_SCORE_MIN
        and metrics["edge_density"] >= COLLAGE_EDGE_DENSITY_MIN
    ):
        add_reason("multi_image_or_collage")
    if (
        metrics["straight_line_count"] >= UI_STRAIGHT_LINE_COUNT_MIN
        and metrics["large_uniform_blocks_ratio"] >= UI_UNIFORM_BLOCKS_RATIO_MIN
        and metrics["edge_density"] >= UI_EDGE_DENSITY_MIN
    ):
        add_reason("screenshot_or_ui_image")

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
                    raise ImageUnavailableError

                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type and not content_type.lower().startswith("image/"):
                    raise ImageUnavailableError

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
        raise ImageUnavailableError from exc
    except httpx.HTTPError as exc:
        raise ImageUnavailableError from exc

    if not chunks:
        raise ImageUnavailableError

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

    try:
        if file is not None:
            file_bytes = await _read_uploaded_file(file)
        elif image_url:
            file_bytes = await _download_image_url(image_url)
        else:
            raise HTTPException(status_code=400, detail="missing_file_or_image_url")

        image = _load_image(file_bytes)
    except ImageUnavailableError:
        return _skip_response("image_unavailable")

    metrics = calculate_metrics(image)
    script_skip, script_reasons = judge_image(metrics)

    return {
        "script_skip": script_skip,
        "script_reasons": script_reasons,
        "metrics": metrics,
    }
