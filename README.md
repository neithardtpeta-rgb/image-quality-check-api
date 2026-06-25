# Image Quality Check API

用于 Dify 工作流 HTTP Request 节点上传图片后做预筛，返回图片质量指标和是否跳过。

## 接口

- Method: `POST`
- Path: `/image-check`
- Content-Type: `multipart/form-data`
- 表单字段名: `file`
- 支持文件类型: `jpg`, `jpeg`, `png`, `webp`

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

注意：字段名必须是 `file`。

```bash
curl -X POST "http://127.0.0.1:8000/image-check" \
  -F "file=@/absolute/path/to/test.jpg"
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

1. 打开 https://render.com/ 。
2. 创建 `New Web Service`，选择本 GitHub 仓库。
3. 配置：
   - Language: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. 点击 Deploy。
5. 部署完成后，接口地址为：

```text
https://你的-render域名/image-check
```

公网测试：

```bash
curl -X POST "https://你的-render域名/image-check" \
  -F "file=@/absolute/path/to/test.jpg"
```

## 部署到 Railway

1. 打开 https://railway.com/ 。
2. 创建 `New Project`，选择 `Deploy from GitHub repo`。
3. 选择本仓库。
4. Railway 会读取 `railway.json`，使用以下启动命令：

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

5. 部署完成后，进入服务的 `Settings -> Networking`，点击 `Generate Domain` 生成公网域名。
6. 接口地址为：

```text
https://你的-railway域名/image-check
```

## Dify HTTP Request 配置

- 请求方法：`POST`
- URL：部署后的 `/image-check` 地址
- Body 类型：`form-data`
- 文件字段名：`file`
- 下游节点读取：`script_skip`、`script_reasons`、`metrics`
