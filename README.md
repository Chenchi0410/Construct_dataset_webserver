# ParseBench 数据集构建器

本地 WebServer，用同名 PDF 与黄金 Markdown 创建两种 ParseBench 数据集：

- Sidecar：`PDF + MD + .test.json`
- JSONL：`PDF + text_content.jsonl + text_formatting.jsonl + table.jsonl`

## 功能

- 单文件与多文件上传，按不区分大小写的文件主名配对。
- 自动生成全文词/句/数字、关键词句、阅读顺序规则。
- 内容规则直接调用目标 ParseBench 运行时提取，确保代码块、链接、表格计数与评测一致。
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

## 兼容性回归测试

以下测试使用目标 ParseBench 自己的规则类执行全部自动生成规则；同一份 Gold Markdown
必须得到 100%：

```powershell
& C:\Users\sangzs1\ParseBench-main\ParseBench-main\.venv\Scripts\python.exe `
  .\tests\parsebench_self_score.py
```

构建会话保存在内存中，默认两小时失效。上传内容不会写入项目目录。

默认兼容目标为 `C:\Users\sangzs1\ParseBench-main\ParseBench-main`。如路径不同，可设置
`PARSEBENCH_ROOT`；如需指定解释器，可设置 `PARSEBENCH_PYTHON`。

## 输出结构

```text
dataset_complete.zip
├── document.pdf
├── document.md
├── document.test.json
├── text_content.jsonl
├── text_formatting.jsonl
├── table.jsonl
├── manifest.jsonl
└── validation_report.json
```

所有文件均位于 ZIP 根目录。注意：完整包同时包含 `.test.json` 与 JSONL 时，原生
ParseBench 加载器会优先使用 Sidecar；需要强制使用 JSONL 时，请下载“JSONL ZIP”。

界面不再收集文档类型和难度。移除它们不影响规则评分，但导出结果不再支持按这两个标签
筛选或分组统计；部门标签仍然保留。

## 规则边界

当前版本只从 Markdown 确定性生成内容、语义格式和表格规则。内容规则遵循目标
ParseBench 的实际行为：普通代码块同时参与内容与 `is_code_block` 格式评测，
`mermaid`/`description` 代码块不进入内容词句。图表数据点、PDF 坐标布局、页眉页脚判断
需要额外视觉或分页标注，不会自动臆测生成。
