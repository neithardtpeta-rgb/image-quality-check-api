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
    "brightness_mean": 128.2,
    "overexposed_ratio": 0.02,
    "underexposed_ratio": 0.01,
    "contrast_std": 52.3
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
| `brightness_mean` | 灰度平均亮度 |
| `overexposed_ratio` | 灰度值大于 245 的像素比例 |
| `underexposed_ratio` | 灰度值小于 15 的像素比例 |
| `contrast_std` | 灰度标准差，值越低对比度越弱 |

## 判定规则

命中任意规则时，`script_skip` 返回 `true`，并把对应原因加入 `script_reasons`。

| 规则 | reason |
| --- | --- |
| `short_side < 512` | `resolution_low` |
| `blur_score < 80` | `blur_or_low_quality` |
| `brightness_mean < 35` | `too_dark` |
| `brightness_mean > 220` | `too_bright` |
| `overexposed_ratio > 0.35` | `overexposure` |
| `underexposed_ratio > 0.45` | `underexposure` |
| `contrast_std < 18` | `low_contrast` |

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
