# ParseBench 数据集构建器

本地 WebServer，用同名 PDF 与黄金 Markdown 创建两种 ParseBench 数据集：

- Sidecar：`PDF + MD + .test.json`
- JSONL：`text_content.jsonl + text_formatting.jsonl + table.jsonl + docs/`

## 功能

- 单文件与多文件上传，按不区分大小写的文件主名配对。
- 自动生成全文词/句/数字、关键词句、阅读顺序规则。
- 自动识别标题、粗体、斜体、删除线、代码块及常见 HTML 格式。
- 将 Markdown 管道表格转换为 ParseBench 表格评测所需 HTML。
- 在线预览和编辑 `.test.json`，再确定性编译为 JSONL。
- 导出 Sidecar ZIP、JSONL ZIP 或包含两者的完整 ZIP。
- 导出前校验规则类型、ID 唯一性及 JSON 结构。

## 启动

在 PowerShell 中运行：

```powershell
cd C:\Users\sangzs1\Construct_dataset_webserver
.\run.ps1
```

首次运行会创建 `.venv` 并安装 `requirements.txt`。然后访问：

```text
http://127.0.0.1:8000
```

也可以手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API

- `GET /api/health`：服务状态。
- `POST /api/analyze`：上传 `pdf_files`、`md_files`，生成 Sidecar 草稿。
- `POST /api/export/{session_id}?mode=full|sidecar|jsonl`：根据确认后的 Sidecar 导出 ZIP。

构建会话保存在内存中，默认两小时失效。上传内容不会写入项目目录。

## 输出结构

```text
dataset_complete.zip
├── sidecar/<department>/
│   ├── document.pdf
│   ├── document.md
│   └── document.test.json
├── parsebench_jsonl/
│   ├── docs/<department>/document.pdf
│   ├── text_content.jsonl
│   ├── text_formatting.jsonl
│   └── table.jsonl
├── manifest.jsonl
└── validation_report.json
```

注意：同一目录同时存在 `.test.json` 与根级 JSONL 时，ParseBench 会优先加载 Sidecar。因此完整包将两种格式放在独立子目录中。

## 规则边界

当前版本只从 Markdown 确定性生成内容、语义格式和表格规则。图表数据点、PDF 坐标布局、页眉页脚判断需要额外视觉或分页标注，不会自动臆测生成。

