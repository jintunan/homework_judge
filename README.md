# 知卷 · 作业批改 Agent

单机服务器版作业批改系统。前端使用 React，后端已迁移为 Python/FastAPI；SQLite、试卷文件和阿里云百炼 API Key 全部保存在服务器端。

首版只支持：

- 初中数学：选择题、填空题、简单简答题；
- 高中物理：选择题、填空题、计算题；
- 固定试卷模板；
- 教师上传参考答案，或由 Agent 先联网检索、无可靠结果时再用模型生成；
- 自动识别和初评后必须由教师逐题确认；
- 教师可修改答案、评分点、分数和批注；
- 保存模型原始响应、解析结果、评分理由和教师最终结果。

暂不支持其他科目、作图题、复杂证明、实验设计、开放探究、多模板混排、多人协作或无人审核发布。

## 页面与工作流

1. 创建任务，选择科目，上传固定模板试卷。
2. 选择答案来源：
   - `reference_upload`：同时上传教师参考答案，模型按题号抽取答案和评分点；
   - `agent_search`：只提取题目结构，逐题联网检索；没有带可靠来源的直接答案时回退到模型生成。
3. 教师在左侧原卷、右侧逐题草稿页面审核题号、题型、满分、答案和评分点。
4. 全部题目审核通过后发布不可变答案版本。
5. 批量上传学生试卷，模型识别和初评。
6. 教师逐题改分、改答案、写批注并确认整卷。
7. 查看学生报告和班级统计。报告只使用教师最终确认结果。

答案修订会创建新版本；已上传的学生试卷继续绑定原答案版本，因此旧报告不会被新版本改写。

## Python 后端

- Web：FastAPI、Uvicorn
- 配置与校验：Pydantic
- HTTP：HTTPX 异步客户端
- 数据库：SQLite、aiosqlite、显式 SQL
- PDF：pypdfium2（PDFium）
- 图像：Pillow
- 后台任务：有界 `asyncio.Queue`
- 前端：React、Vite、TanStack Query

PDF 不再经过 PDF.js 的 `document.destroy()` 生命周期，因此不会出现 `document.destroy is not a function`。试卷结构解析按题目节点容错：单题坏节点不会让整卷失败；参考答案评分点超分会按 Decimal 比例归一化并保留调整记录；只有零道可用题目时才会额外请求一次纯文本结构修复。

## 首次安装

要求：

- Windows；
- Python 3.12；
- Node.js 20+；
- npm。

在项目根目录执行：

```powershell
D:\Python\python.exe -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
npm install
Copy-Item .env.example .env
```

如果不用 `D:\Python\python.exe`，请替换为本机 Python 3.12 路径。

编辑 `.env`，至少填写：

```dotenv
APP_ENV=production
DASHSCOPE_API_KEY=sk-你的百炼APIKey
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3.7-plus
DASHSCOPE_NATIVE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_SEARCH_MODEL=qwen-plus
```

API Key 只从服务端环境变量读取，不写入 SQLite、请求快照、日志或浏览器响应。

## 启动

开发模式：

```powershell
npm run dev
```

- React：http://127.0.0.1:5173
- API：http://127.0.0.1:8787

生产模式：

```powershell
npm run build
npm start
```

生产页面和 API 均位于 http://127.0.0.1:8787。默认只监听本机；局域网或公网部署应在前方配置反向代理、HTTPS、身份认证与访问控制。

## 数据目录

默认结构：

```text
data/
├── homework-judge.sqlite
├── uploads/
│   ├── templates/
│   ├── reference-answers/
│   └── submissions/
└── tmp/
```

相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8787` | 服务端口 |
| `APP_DATA_DIR` | `./data` | 数据根目录 |
| `DATABASE_PATH` | `APP_DATA_DIR/homework-judge.sqlite` | SQLite 路径 |
| `UPLOAD_DIR` | `APP_DATA_DIR/uploads` | 上传目录 |
| `TEMP_DIR` | `APP_DATA_DIR/tmp` | 临时目录 |
| `TEACHER_NAME` | `本机教师` | 发布与成绩确认人 |
| `MAX_UPLOAD_MB` | `20` | 单文件大小上限 |
| `MAX_FILES_PER_BATCH` | `50` | 单批学生试卷数 |
| `MAX_PDF_PAGES` | `20` | 单份 PDF 页数上限 |
| `GRADING_CONCURRENCY` | `2` | 学生试卷并发数 |
| `ANSWER_CONFIG_CONCURRENCY` | `2` | 答案处理并发数 |
| `MODEL_TIMEOUT_MS` | `120000` | 单次模型请求超时 |
| `LOW_CONFIDENCE_THRESHOLD` | `0.65` | 初评重点复核阈值 |
| `ANSWER_SEARCH_CONFIDENCE_THRESHOLD` | `0.72` | 检索答案可靠性阈值 |

## 从旧 Node 后端切换

不要让 Node 和 Python 同时写同一数据库。

当前工作区已于 2026-07-26 完成正式库 v2→v3 切换；切换前备份位于
`data/backups/python-cutover-20260726-1525`。在其他机器或使用其他数据库部署时，
仍须完整执行以下步骤。

1. 停止旧 Node 服务。
2. 备份 SQLite 主文件及同目录的 `-wal`、`-shm` 文件。
3. 备份整个 `data/uploads`。
4. 先对数据库副本执行 Python 启动和验证。
5. 确认表计数、外键、任务、报告和统计一致后，再让 Python 使用正式数据库。
6. 启动 `npm start`。

Python 启动时把 schema 升级到 v3，并执行 `PRAGMA foreign_key_check`。v3 新增：

- 版本级识别问题与未解决数；
- 逐题解析问题、归一化记录和必须修正标记；
- `structure_repair` 运行类型。

迁移是事务性的；失败时回滚。

回滚步骤：

1. 停止 Python 服务。
2. 保存失败现场的数据库副本和日志。
3. 恢复切换前备份的 SQLite、WAL、SHM 与 uploads。
4. 启动旧服务或修复后重新迁移。

## 验证命令

```powershell
npm run typecheck
npm run lint
npm test
npm run build
npm run test:model
```

`test:model` 只检查服务端是否读取到模型配置，不发送计费请求。完整真实模型验证必须由教师明确发起，会产生百炼费用。

本次已用 7 页高中物理 PDF 验证 Python/PDFium 渲染，页数与首尾页面均可正常输出。

## 常见问题

- `ANSWER_EXTRACTION_INVALID`：查看答案配置运行历史。系统会先本地容错；零道可用题目时只做一次结构修复。修复仍失败可重新识别，新版本会保留旧运行。
- 评分点合计超过满分：上传参考答案模式会等比例归一化并显示调整前后值；教师仍需确认。
- 无参考答案时模型提前给出了答案：系统会清空该答案，再执行“联网检索优先、模型生成回退”。
- 401/403：检查 Key、工作空间权限、地域和接口地址。
- 模型超时：减少 PDF 页数或扫描图大小，或提高 `MODEL_TIMEOUT_MS`。
- 学生姓名为“待补充姓名”：教师补录姓名后才能确认整卷。
- 成绩无法确认：所有题目都必须标记为“已复核”，并且学生姓名已确认。

## 主要 API

- `GET /api/health`
- `GET /api/model/status`
- `POST /api/tasks`
- `POST /api/tasks/:taskId/answer-config-runs`
- `GET /api/tasks/:taskId/answer-config`
- `PATCH /api/answer-drafts/:draftId`
- `POST /api/answer-drafts/:draftId/approve`
- `POST /api/answer-drafts/:draftId/research`
- `POST /api/answer-drafts/:draftId/regenerate`
- `POST /api/tasks/:taskId/answer-config/approve`
- `POST /api/tasks/:taskId/answer-config/revise`
- `POST /api/tasks/:taskId/submissions`
- `POST /api/tasks/:taskId/grading-runs`
- `GET /api/submissions/:submissionId/review`
- `PATCH /api/submissions/:submissionId/reviews/:questionId`
- `POST /api/submissions/:submissionId/confirm`
- `GET /api/submissions/:submissionId/report`
- `GET /api/tasks/:taskId/statistics`
