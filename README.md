# Image Quality Check API

用于 Dify 工作流 HTTP Request 节点上传图片后做预筛，返回图片质量指标和是否跳过。

## 接口

- Method: `POST`
- Path: `/image-check`
- 支持 `multipart/form-data` 上传文件
- 支持 `application/json` 传入图片 URL
- 支持 `multipart/form-data` 传入图片 URL
- 文件字段名: `file`
- URL 字段名: `image_url`
- 支持文件类型: `jpg`, `jpeg`, `png`, `webp`

处理优先级：

1. 如果传入 `file`，优先使用 `file`。
2. 如果没有 `file`，但传入 `image_url`，下载 URL 对应图片后检测。
3. 如果 `file` 和 `image_url` 都没有，返回 `400`。

URL 限制：

- 只允许 `http` 和 `https`。
- 下载超时时间为 10 秒。
- URL 图片最大下载大小为 10MB。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## curl 测试

### 本地 file 模式

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -F "file=@/absolute/path/to/test.jpg"
```

### 本地 JSON image_url 模式

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://raw.githubusercontent.com/neithardtpeta-rgb/image-test-dataset/main/001.jpg"}'
```

### 本地 form-data image_url 模式

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -F "image_url=https://raw.githubusercontent.com/neithardtpeta-rgb/image-test-dataset/main/001.jpg"
```

### Render JSON image_url 模式

```bash
curl -X POST "https://image-quality-check-api.onrender.com/image-check" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://raw.githubusercontent.com/neithardtpeta-rgb/image-test-dataset/main/001.jpg"}'
```

### Render file 模式

```bash
curl -X POST "https://image-quality-check-api.onrender.com/image-check" \
  -F "file=@001.jpg"
```

返回示例：

```json
{
  "script_skip": false,
  "script_reasons": [],
  "metrics": {
    "width": 1024,
    "height": 1024,
    "short_side": 1024,
    "blur_score": 180.5,
    "center_blur_score": 165.2,
    "resized_blur_score": 132.9,
    "brightness_mean": 128.2,
    "overexposed_ratio": 0.02,
    "underexposed_ratio": 0.01,
    "contrast_std": 52.3,
    "center_contrast_std": 48.1,
    "edge_density": 0.07,
    "small_component_count": 320,
    "high_frequency_density": 0.04,
    "aspect_ratio": 1.0,
    "image_area": 1048576,
    "border_line_density": 0.03,
    "straight_line_count": 0,
    "grid_line_score": 0.0,
    "large_uniform_blocks_ratio": 0.12
  }
}
```

## 检测指标

| 字段 | 含义 |
| --- | --- |
| `width` | 图片宽度 |
| `height` | 图片高度 |
| `short_side` | 宽高中的短边 |
| `blur_score` | Laplacian variance，值越低越模糊 |
| `center_blur_score` | 中心 60% 区域 Laplacian variance，用于降低水印/边缘对主体判断的干扰 |
| `resized_blur_score` | 最长边缩放到 512 后的 Laplacian variance，用于削弱密集小字造成的虚假锐利 |
| `brightness_mean` | 灰度平均亮度 |
| `overexposed_ratio` | 灰度值大于 245 的像素比例 |
| `underexposed_ratio` | 灰度值小于 15 的像素比例 |
| `contrast_std` | 灰度标准差，值越低对比度越弱 |
| `center_contrast_std` | 中心区域灰度标准差 |
| `edge_density` | 分析图上的边缘密度 |
| `small_component_count` | 高频小连通区域数量，用于识别密集文字/水印纹理 |
| `high_frequency_density` | 高频像素比例 |
| `aspect_ratio` | 宽高比 |
| `image_area` | 图片总像素面积 |
| `border_line_density` | 边框区域边缘密度 |
| `straight_line_count` | 明显水平/垂直线数量 |
| `grid_line_score` | 网格/直线结构强度 |
| `large_uniform_blocks_ratio` | 大块纯色/低变化区域比例 |

## 判定规则

命中任意规则时，`script_skip` 返回 `true`，并把对应原因加入 `script_reasons`。

| 规则 | reason |
| --- | --- |
| 图片无法下载、HTTP 非 200、响应不是图片、图片无法解码 | `image_unavailable` |
| `short_side < 384` | `resolution_too_small` |
| `width * height < 200000` | `image_area_too_small` |
| `aspect_ratio > 4` 或 `aspect_ratio < 0.25` | `extreme_aspect_ratio` |
| `brightness_mean >= 185` 且 `overexposed_ratio >= 0.08` | `overexposed_or_washed_out` |
| `brightness_mean >= 190` 且 `contrast_std < 55` | `washed_out_low_contrast` |
| `brightness_mean >= 200` 且 `contrast_std < 65` | `severely_washed_out` |
| `brightness_mean <= 45` 且 `underexposed_ratio >= 0.35` | `underexposed_too_dark` |
| `contrast_std < 25` | `low_contrast` |
| `center_blur_score < 80` 且 `center_contrast_std < 35` | `center_subject_blurry` |
| `resized_blur_score < 60` | `global_blurry_after_resize` |
| `blur_score` 较高但画面发白低对比 | `false_sharpness_from_overlay_or_texture` |
| 高频小组件多、边缘密度高、画面偏亮 | `dense_watermark_or_text_overlay` |
| 高频小组件多、低对比、高频密度高 | `overlay_texture_affects_subject` |
| 明显网格分割、多矩形拼贴结构 | `multi_image_or_collage` |
| 明显水平/垂直线、UI 色块、截图结构 | `screenshot_or_ui_image` |

阈值集中写在 `main.py` 顶部，后续可以直接调整常量。

## 测试样例说明

### 正常图片，应该 `script_skip=false`

```bash
curl -X POST "https://image-quality-check-api.onrender.com/image-check" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://raw.githubusercontent.com/neithardtpeta-rgb/image-test-dataset/main/001.jpg"}'
```

预期：返回 `script_skip=false`，`script_reasons=[]`。

### 小尺寸图片，应该 `script_skip=true`

准备一张短边小于 384 或总面积小于 200000 的图片，然后执行：

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -F "file=@small.jpg"
```

预期：`script_reasons` 包含 `resolution_too_small` 或 `image_area_too_small`。

### 过曝发白图片，应该 `script_skip=true`

准备一张整体偏白、低对比的图片，然后执行：

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -F "file=@washed_out.jpg"
```

预期：`script_reasons` 包含 `washed_out_low_contrast`、`severely_washed_out` 或 `overexposed_or_washed_out`。

### 高 `blur_score` 但主体发白、水印密集，应该 `script_skip=true`

准备一张亮度高、主体发白、覆盖密集小字/水印的图片，然后执行：

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -F "file=@washed_watermark.jpg"
```

预期：即使 `blur_score` 很高，`script_reasons` 仍应包含 `false_sharpness_from_overlay_or_texture`，明显水印时还应包含 `dense_watermark_or_text_overlay` 或 `overlay_texture_affects_subject`。

## 部署到 Render

1. 把本项目推送到 GitHub。
2. 在 Render 创建 `New Web Service`，选择对应 GitHub 仓库。
3. Runtime 选择 `Python 3`。
4. Build Command 填：

```bash
pip install -r requirements.txt
```

5. Start Command 填：

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

6. 部署完成后，用公网地址测试：

```bash
curl -X POST "https://你的-render域名/image-check" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://raw.githubusercontent.com/neithardtpeta-rgb/image-test-dataset/main/001.jpg"}'
```

## 部署到 Railway

1. 把本项目推送到 GitHub。
2. 在 Railway 创建 `New Project`，选择 `Deploy from GitHub repo`。
3. 进入项目变量配置，如需手动指定启动命令，设置：

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

4. Railway 会自动执行依赖安装；如果没有自动识别，确认仓库根目录存在 `requirements.txt`。
5. 部署完成后，用公网地址测试：

```bash
curl -X POST "https://你的-railway域名/image-check" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://raw.githubusercontent.com/neithardtpeta-rgb/image-test-dataset/main/001.jpg"}'
```

## Dify HTTP Request 配置要点

- 请求方法选择 `POST`。
- URL 使用部署后的 `/image-check` 地址。
- 上传文件场景：Body 类型选择 `form-data`，文件字段名填写 `file`。
- CSV URL 场景：Body 类型选择 `JSON`，传入 `{"image_url":"图片URL"}`。
- 下游节点读取 `script_skip` 和 `script_reasons` 做分流。
