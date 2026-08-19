# 作业识别与批改工作台

本地教师工作台：识别试卷与参考答案、匹配题目、对齐学生原卷，并按确认题型完成自动批改、风险复核、原卷批注和错题分析。

## 首版范围

- 输入：PDF、DOCX、JPG、PNG 格式的试卷和参考答案。
- 答案文件：兼容“题号＋答案/解析”精简版及包含完整题干的解析版。
- 批改题型：单选、多选、填空和计算题。
- 多选题：错选为零；少选且未错选时，按选对数量占正确选项数量的比例给分，每题保留两位小数。
- 填空题：逐空独立给分，支持教师同义答案、科学计数法、单位换算和公式等价验证；非完全一致答案交给模型判断。
- 计算题：使用教师确认并冻结的评分点；首版采用严格依赖扣分，前置点错误时依赖它的后续点不给分。
- 输出：逐题得分与原因、教师复核项、带勾/红圈/部分分标记的批注试卷 PDF，以及不展开完整答案的错题分析 PDF。
- 不包含：独立答题卡、题序变化、作文/证明/作图题、联网搜题、班级排名和长期学情分析。

## 环境

- Windows
- Python 3.12
- Node.js 20 或更高
- LibreOffice 或 Microsoft Word（处理 DOCX 时需要）
- 阿里云百炼 API Key

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm install
Copy-Item .env.example .env
```

在 `.env` 中填写 `DASHSCOPE_API_KEY`，并将 `GRADING_ENABLED=true` 开启批改。若使用百炼 Workspace 专属域名，同时修改 `DASHSCOPE_BASE_URL`。服务端默认模型为 `qwen3-vl-plus`，可通过 `DASHSCOPE_MODEL` 调整。

Windows 上会优先使用 LibreOffice，没有 LibreOffice 时自动使用 Microsoft Word。若 LibreOffice 安装在非标准目录，请配置：

```text
SOFFICE_PATH=C:\Program Files\LibreOffice\program\soffice.exe
```

### 学生答卷识别并发

`STUDENT_RECOGNITION_CONCURRENCY` 控制单份学生答卷同时识别的题目数，取值为 1 到 3，默认 3。`MODEL_CONCURRENCY` 是整个服务共享的模型请求上限，默认 3；实际并发会服从两者中更严格的限制。修改 `.env` 后需要重启服务才能生效。

## 开发运行

```powershell
pnpm dev
```

浏览器打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。

## 生产运行

```powershell
pnpm build
pnpm start
```

浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。

## 验证

```powershell
pnpm run lint
pnpm test
```

运行数据默认保存在 `data/runtime/`，测试样本目录 `data/dataset/` 不会被修改。API Key 只由服务端环境读取，不会保存到 SQLite 或返回浏览器。

## 匹配规则

1. 试卷和答案中的规范化题号均唯一时，优先建立题号匹配。
2. 题号缺失时，只有题干相似度达到阈值且第一候选明显领先时才给出建议。
3. 重复题号、答案竞争、低相似度和孤立答案均进入人工处理。
4. 自动匹配不会直接确认；全部题目经教师确认且没有未处理答案后才能完成任务。

## 批改流程

1. 在题目复核页确认题型、分值与标准答案；填空题配置每空分值和可接受答案，计算题生成、编辑并冻结评分细则。
2. 进入“学生答卷”，上传与原卷同版的学生试卷，等待页面对齐、逐题区域映射与作答识别完成。
3. 点击“进入批改工作台”并启动批改。单选、多选走确定性规则；填空和计算题按需调用模型及验证工具。
4. 只处理待复核题：可确认当前结论、修正选择题识别、逐空改判或逐评分点改判。系统重新执行依赖传播和分数审计。
5. 完成后预览或下载“批注试卷”和“错题分析”。教师改分或修改错误位置后，旧文件自动过期，需要重新生成。

批改运行使用现有 Python 状态流水线和 SQLite 检查点实现，首版不依赖 LangGraph。题型路由、工具接口和状态契约保持独立，后续出现跨服务、长时间暂停或复杂人工审批时再迁移到 LangGraph。

## 常见问题

- `MODEL_NOT_CONFIGURED`：检查 `.env` 中的 `DASHSCOPE_API_KEY`。
- `MODEL_AUTH_FAILED`：检查 Key、地域和 Base URL 是否属于同一百炼业务空间。
- `DOCX_CONVERTER_MISSING`：安装 LibreOffice/Microsoft Word，或设置 `SOFFICE_PATH`。
- `DOCUMENT_TOO_MANY_PAGES`：调整 `MAX_DOCUMENT_PAGES`，并考虑模型调用成本。
- `GRADING_DISABLED`：在 `.env` 中设置 `GRADING_ENABLED=true` 并重启服务。
- `FROZEN_RUBRIC_REQUIRED`：先为计算题确认并冻结评分细则。
- `GRADING_REVIEW_REQUIRED`：先处理全部待复核题，再生成批注与报告。
- `ANNOTATION_ERROR_LOCATION_REQUIRED`：该非满分题缺少可靠错误位置，需要教师在原图上确认后再生成。
- 处理中断：重新启动服务后任务会显示为可重试，不会自动再次调用模型。
