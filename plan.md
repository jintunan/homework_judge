# Python 后端迁移与试卷结构容错解析 Plan

## 架构概览

保留现有 React/Vite 前端、`shared/contracts.ts` 前端类型、本地上传目录和 SQLite 正式数据，把 Express/TypeScript 服务替换为单个 Python ASGI 服务。生产环境由一个 Python 进程同时提供 `/api` 和 `dist/client`，开发环境继续并行启动 Python API 与 Vite。

```text
React / Vite
    │ 现有 JSON / multipart API
    ▼
FastAPI 路由与统一错误层
    ├── 任务、文件、答案配置、复核、报告
    ├── 答案配置后台队列
    └── 学生批改后台队列
          │
          ├── PDFium / Pillow 文件处理
          ├── 百炼视觉、原生搜索、答案生成
          ├── JSON 提取与逐题容错归一化
          └── SQLite 仓储、版本发布与审计
```

Python 服务使用 FastAPI 与 Uvicorn，Pydantic 负责 API 输入输出，HTTPX 负责百炼请求，`pypdfium2` 与 Pillow 负责 PDF/图像转换，`aiosqlite` 负责异步 SQLite 访问。后台任务采用进程内有界队列和固定数量 worker，不引入 Celery、Redis 或第二个服务。

## 核心数据结构

### Settings

由环境变量加载的只读配置：

```python
class Settings(BaseSettings):
    port: int
    app_data_dir: Path
    database_path: Path
    upload_dir: Path
    temp_dir: Path
    teacher_name: str
    max_upload_mb: int
    max_files_per_batch: int
    max_pdf_pages: int
    grading_concurrency: int
    answer_config_concurrency: int
    model_timeout_ms: int
    low_confidence_threshold: float
    answer_search_confidence_threshold: float
    dashscope_api_key: SecretStr | None
    dashscope_base_url: AnyHttpUrl
    dashscope_native_base_url: AnyHttpUrl
    dashscope_model: str
    dashscope_search_model: str
```

配置初始化时解析所有路径为绝对路径，但对外状态接口只返回脱敏布尔值、模型名和区域提示。

### ApiSuccess / ApiFailure

所有公开接口保持现有 envelope：

```python
class ApiSuccess[T](BaseModel):
    ok: Literal[True] = True
    data: T

class ApiFailure(BaseModel):
    ok: Literal[False] = False
    error: ApiErrorBody
```

`ApiErrorBody` 包含 `code`、`message` 和可选 `fields`。未知异常只返回 `INTERNAL_ERROR`，调用栈仅写入受控服务端日志。

### ParseIssue

描述一次本地结构恢复或逐题归一化：

```python
class ParseIssue(BaseModel):
    path: list[str | int]
    code: str
    message: str
    severity: Literal["attention", "blocking", "skipped"]
    original_value: Any | None
    normalized_value: Any | None
    requires_correction: bool
```

`original_value` 与 `normalized_value` 在持久化前执行长度限制和敏感字段过滤。前端仅显示教师需要的局部值。

### NormalizedQuestion

解析器的统一逐题结果：

```python
class NormalizedQuestion(BaseModel):
    question_number: str
    question_text: str
    type: QuestionType
    max_score: Decimal
    standard_answer: str
    scoring_points: list[ScoringPoint]
    reason: str
    confidence: float
    needs_attention: bool
    requires_correction: bool
    issues: list[ParseIssue]
    source_index: int
```

在 `agent_search` 模式下，`standard_answer` 和 `scoring_points` 在 schema 最终校验前强制清空。在 `reference_upload` 模式下，合法评分点可按满分归一化。

### ParsedPaper

```python
class ParsedPaper(BaseModel):
    questions: list[NormalizedQuestion]
    issues: list[ParseIssue]
    overall_note: str | None
    candidate_shape: Literal["object", "array"]
    repaired: bool
```

只要 `questions` 非空就可以创建审核草稿。`issues` 中的阻塞项写入答案版本，并阻止发布，直到重新识别产生无阻塞的新版本。

### AnswerResolutionRun

现有运行类型扩展为：

```python
AnswerRunKind = Literal[
    "exam_extraction",
    "reference_extraction",
    "structure_repair",
    "web_search",
    "model_generation",
]
```

`parsed_output_json` 保存 `ParsedPaper` 或单题解析结果，`raw_response_json` 保存百炼原始响应，二者不可由教师修改。

### JobKey / JobRuntimeState

进程内去重与进度结构：

```python
JobKey = tuple[Literal["answer", "grading", "draft"], str]

class JobRuntimeState(BaseModel):
    answer_tasks: int
    grading_tasks: int
    questions: int
```

运行集合只用于阻止同一目标重复入队；可恢复状态始终以 SQLite 为准。

## 试卷结构解析设计

### 1. 模型文本提取

百炼兼容接口客户端从 `choices[0].message.content` 获取内容：

- 字符串直接使用。
- 文本块数组按顺序拼接其中的 `text`。
- 缺少可用文本时产生 `MODEL_RESPONSE_EMPTY`。
- 原始响应在任何解析前写入运行记录。

### 2. 安全 JSON 候选提取

`JsonCandidateExtractor` 按以下顺序尝试：

1. 对完整文本执行 `json.loads`。
2. 提取一个或多个 Markdown `json` 代码块。
3. 使用 `json.JSONDecoder.raw_decode` 从每个 `{` 或 `[` 候选位置扫描完整 JSON 值。
4. 优先选择含 `questions`、`题目` 或数组根节点的候选。

禁止 `eval`、`ast.literal_eval`、JSON5 任意扩展和正则拼接对象。提取失败时返回包含候选位置的诊断，不记录完整响应到普通日志。

### 3. 根节点和字段别名

`PaperShapeAdapter` 支持：

- `{"questions": [...]}`；
- `{"题目": [...]}`；
- 直接的题目数组；
- `questionNumber / number / 题号`；
- `questionText / text / stem / 题干`；
- `type / questionType / 题型`；
- `maxScore / score / 满分`；
- `standardAnswer / answer / 标准答案`；
- `scoringPoints / points / 评分点`；
- `confidence / 置信度`；
- `needsAttention / 需关注`。

别名只做字段映射，不推断缺失的完整题目内容。

### 4. 逐题归一化

每个题目节点独立处理：

- 题号接受字符串或数字，统一为去空格字符串。
- 缺失或重复题号生成 `待核-{序号}`，设置 `requires_correction=true`。
- 题型接受现有英文枚举和约定中文/英文别名；未知题型映射为当前科目的主观题兜底类型，并要求教师修正。
- 满分接受数字或数字字符串，必须是有限正数且不超过配置上限；无法恢复时跳过该节点并记录 blocking issue。
- 置信度缺失时为 `0`，超出范围时夹取到 `[0, 1]` 并标记关注。
- 布尔值接受布尔类型及 `"true" / "false" / "是" / "否"`。
- 缺少题干或节点不是对象时跳过并记录位置，不影响其他题目。

### 5. 答案模式规则

`agent_search`：

- 在评分点 schema 校验前把 `standard_answer` 置为空字符串、`scoring_points` 置为空数组。
- 若模型提前生成答案或评分点，写入 `ParseIssue(code="unexpected_answer_dropped")`，但不标记 blocking。
- 草稿创建后继续逐题执行原生联网搜索，未命中再模型生成。

`reference_upload`：

- 丢弃描述为空、分值非数字、非有限或负数的评分点。
- 若合法评分点合计超过满分，以 `max_score / total` 等比例缩放；除最后一项外向下保留两位小数，最后一项使用剩余分值，保证合计精确不超过满分。
- 自动调整写入 issue 和 normalization 记录，`needs_attention=true`，但教师可通过明确审核接受调整结果。
- 标准答案缺失时要求教师修正，不能审核通过。

### 6. 局部失败与结构修复

- 首次解析得到至少一道可用题目：创建所有可用草稿并保存局部问题；不调用结构修复。
- 没有任何可用题目：创建一条 `structure_repair` 运行，只发送原始模型文本、科目、答案模式和目标 JSON schema，不发送图像、文件路径、任务名、班级或教师信息。
- 修复响应只再执行一次本地解析，不允许递归修复。
- 修复仍无可用题目时，两个运行均留档，答案配置状态改为 `failed`，错误响应提供运行 ID、路径和摘要。

## 数据库设计与迁移

### schema v3

从 `PRAGMA user_version = 2` 幂等迁移到 v3：

1. `answer_config_versions` 新增：
   - `extraction_issues_json TEXT NOT NULL DEFAULT '[]'`
   - `unresolved_issue_count INTEGER NOT NULL DEFAULT 0`
2. `answer_question_drafts` 新增：
   - `parse_issues_json TEXT NOT NULL DEFAULT '[]'`
   - `normalization_json TEXT NOT NULL DEFAULT '[]'`
   - `requires_correction INTEGER NOT NULL DEFAULT 0`
3. 事务内重建 `answer_resolution_runs`，把 `kind` CHECK 扩展为 `structure_repair`，复制所有旧行并保持 ID、时间和外键。
4. 设置 `PRAGMA user_version = 3`，提交后执行 `PRAGMA foreign_key_check`。

旧版本行的新字段默认为空，不改变任何已发布答案、学生绑定或报告。迁移脚本在 `BEGIN IMMEDIATE` 内运行，任何建表、复制或外键检查错误都回滚。

### 数据库访问

- `Database` 提供连接工厂，为每个请求或后台事务创建独立 `aiosqlite.Connection`。
- 每个连接启用 `foreign_keys=ON`、WAL、`busy_timeout=5000` 和 row factory。
- 多步骤发布、修订、教师终评、迁移使用 `BEGIN IMMEDIATE`。
- 进程内 `asyncio.Lock` 串行化迁移和大事务；普通只读查询可并发。
- 仓储层返回字典/Pydantic DTO，不把游标或连接泄漏给路由层。

## 模块设计

### 配置与启动

**文件：** `backend/homework_judge/config.py`、`main.py`、`run.py`

**职责：**

- 加载并校验配置。
- FastAPI lifespan 中执行迁移、恢复中断任务、创建 HTTPX 客户端和后台 worker。
- 注册 `/api` 路由、错误处理、文件服务和 React catch-all。
- 关闭时停止入队，取消或等待 worker，并持久化未完成运行。

### 数据库与仓储

**文件：** `backend/homework_judge/db/*`

**职责：**

- 最新 schema、v2→v3 迁移和事务辅助。
- 移植任务、文件、答案版本、草稿、运行、来源、提交、评分复核、审计和报告查询。
- 所有 JSON 列统一用 UTF-8 JSON 编解码，字段名转换在 mapper 中完成。

### 文件存储与处理

**文件：** `backend/homework_judge/files/storage.py`、`processor.py`

**职责：**

- multipart 文件数量、扩展名、MIME、文件签名和大小验证。
- UUID 存储名、相对路径持久化和失败清理。
- Pillow 处理 JPG/PNG，PDFium 顺序渲染 PDF 页面。
- 输出与现有模型客户端兼容的 JPEG data URL，并在每页后关闭 bitmap、page、document 和文件句柄。

### 百炼客户端

**文件：** `backend/homework_judge/model/*`

**职责：**

- `DashScopeVisionClient`：视觉识别和学生评分兼容接口。
- `DashScopeNativeSearchClient`：原生联网搜索、来源提取和可靠性判断。
- `AnswerGenerator`：无可靠搜索结果时生成答案。
- 共享异步 HTTPX client、超时、有限重试、错误分类和脱敏请求快照。

### 答案配置

**文件：** `backend/homework_judge/answer_config/*`

**职责：**

- 提示词和版本号。
- JSON 候选提取、字段适配、逐题归一化和解析诊断。
- 视觉提取、一次结构修复、搜索优先/生成回退。
- 答案版本、教师修改、退回、单题重试和事务发布。
- 发布前同时检查草稿审核状态、`requires_correction` 和版本级 unresolved issue。

### 后台任务

**文件：** `backend/homework_judge/jobs/manager.py`

**职责：**

- 答案配置和学生批改使用独立有界队列。
- 按配置数量启动固定 worker，并以 semaphore 约束实际百炼调用。
- 对任务、提交和草稿 ID 去重。
- 任务异常隔离到当前目标，写入数据库后继续处理其他项。
- 提供只读运行态统计给进度接口。

### 学生批改与报告

**文件：** `backend/homework_judge/grading/*`、`reports/statistics.py`

**职责：**

- 按学生提交绑定的答案版本读取正式题目。
- 解析模型初评、保存原始响应和评分理由。
- 教师改分、批注、撤销确认和整卷确认。
- 学生报告、班级版本分组、成绩分段和逐题得分率。

### API

**文件：** `backend/homework_judge/api/*`

保持现有接口：

```text
GET    /api/health
GET    /api/model/status
GET    /api/tasks
POST   /api/tasks
GET    /api/tasks/{task_id}
PUT    /api/tasks/{task_id}
PUT    /api/tasks/{task_id}/questions
GET    /api/files/{file_id}
POST   /api/tasks/{task_id}/answer-config-runs
GET    /api/tasks/{task_id}/answer-config
GET    /api/tasks/{task_id}/answer-config-progress
PATCH  /api/answer-drafts/{draft_id}
POST   /api/answer-drafts/{draft_id}/approve
POST   /api/answer-drafts/{draft_id}/reject
POST   /api/answer-drafts/{draft_id}/research
POST   /api/answer-drafts/{draft_id}/regenerate
POST   /api/tasks/{task_id}/answer-config/approve
POST   /api/tasks/{task_id}/answer-config/revise
GET    /api/answer-runs/{run_id}
GET    /api/tasks/{task_id}/submissions
POST   /api/tasks/{task_id}/submissions
PATCH  /api/submissions/{submission_id}
GET    /api/tasks/{task_id}/grading-progress
POST   /api/tasks/{task_id}/grading-runs
POST   /api/submissions/{submission_id}/retry
GET    /api/submissions/{submission_id}/review
PATCH  /api/submissions/{submission_id}/reviews/{question_id}
POST   /api/submissions/{submission_id}/confirm
GET    /api/submissions/{submission_id}/audit
GET    /api/submissions/{submission_id}/report
GET    /api/tasks/{task_id}/statistics
```

`POST /answer-config-runs` 在首次运行时创建 V1；当前版本失败或存在版本级阻塞解析问题时，重试会把旧草稿版本标为 superseded 并创建新的草稿版本，保留全部旧运行。

向后兼容新增可选字段：

- 答案草稿：`parseIssues`、`normalizations`、`requiresCorrection`
- 答案详情：`extractionIssues`
- 运行类型：`structure_repair`

React 对这些字段增加诊断展示和发布前提示，但原有字段及路径不变。

## 模块交互

### 创建与识别

```text
创建任务
  → 校验并持久化模板/参考答案
  → 创建任务与审计事件
  → 教师启动答案配置
  → 创建答案草稿版本并入队
  → PDF/图片逐页转 JPEG
  → 百炼视觉识别并保存原始响应
  → 本地候选提取与逐题归一化
      ├── 有可用题目：创建草稿
      └── 无可用题目：一次结构修复 → 再解析
  → agent_search：逐题搜索，未命中则生成
  → review_pending / failed
```

### 教师发布

```text
教师修改草稿
  → schema 校验
  → 清除该题 requires_correction
  → 审核通过
  → 检查所有草稿、版本级问题和评分点
  → BEGIN IMMEDIATE
  → 写入不可变 questions
  → 批准版本并切换 active_answer_version_id
  → 写审计
  → COMMIT
```

### 学生批改

```text
上传学生试卷
  → 服务端检查答案配置已批准且无新草稿版本
  → submission 绑定 active_answer_version_id
  → 批改任务入队
  → 按绑定版本读取正式题目
  → 百炼初评并保存原始响应/理由
  → 教师逐题改分批注
  → 整卷确认
  → 报告与统计只读取教师最终结果
```

## 文件组织

```text
homework_judge/
├── backend/
│   ├── homework_judge/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── run.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── schemas.py
│   │   ├── subjects.py
│   │   ├── api/
│   │   │   ├── response.py
│   │   │   ├── health.py
│   │   │   ├── tasks.py
│   │   │   ├── files.py
│   │   │   ├── answer_config.py
│   │   │   ├── submissions.py
│   │   │   ├── grading.py
│   │   │   ├── reviews.py
│   │   │   └── reports.py
│   │   ├── db/
│   │   │   ├── database.py
│   │   │   ├── migrations.py
│   │   │   ├── schema.sql
│   │   │   ├── migrations/003-python-parser.sql
│   │   │   └── repositories/
│   │   ├── files/
│   │   │   ├── storage.py
│   │   │   └── processor.py
│   │   ├── model/
│   │   │   ├── dashscope.py
│   │   │   ├── dashscope_search.py
│   │   │   └── answer_generator.py
│   │   ├── answer_config/
│   │   │   ├── prompts.py
│   │   │   ├── parser.py
│   │   │   ├── normalizer.py
│   │   │   ├── extractor.py
│   │   │   ├── resolver.py
│   │   │   ├── orchestrator.py
│   │   │   └── publisher.py
│   │   ├── grading/
│   │   │   ├── prompt.py
│   │   │   ├── output.py
│   │   │   └── orchestrator.py
│   │   ├── reports/statistics.py
│   │   └── jobs/manager.py
│   └── tests/
│       ├── fixtures/
│       ├── unit/
│       ├── integration/
│       └── conftest.py
├── client/                         # 保留 React
├── shared/                         # 保留前端 TypeScript 契约
├── docs/archive/                   # Node v1 文档归档
├── pyproject.toml
├── requirements.lock
├── package.json                    # 前端与统一开发命令
├── README.md
├── spec.md
├── plan.md
├── task.md
└── checklist.md
```

完成兼容验收后删除运行时 Express 服务、服务端 TypeScript 编译配置和仅针对旧后端的测试；React、共享前端契约和 UI 测试保留。

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| Python Web 框架 | FastAPI + Uvicorn | 原生 ASGI、Pydantic 集成、multipart 与静态文件成熟，适合保留 JSON API |
| 数据访问 | aiosqlite + 显式 SQL | 最大限度复用现有 schema 与查询语义，避免 ORM 对旧数据和复杂统计产生隐式变化 |
| PDF 渲染 | pypdfium2 | Windows 有预编译 wheel，可在 Python 内逐页渲染并显式释放资源，无需依赖系统 Poppler |
| 图像处理 | Pillow | 支持方向纠正、RGB 转换、缩放和 JPEG 编码，Windows 部署简单 |
| 百炼 HTTP | HTTPX AsyncClient | 支持异步、连接复用、超时、取消和可测试 transport |
| 后台任务 | 进程内 asyncio.Queue + 固定 worker | 满足单机 MVP，保留并发控制且无需引入外部服务 |
| 数据校验 | Pydantic 严格最终模型 + 自定义容错适配层 | 将“不可信模型输出恢复”和“正式数据严格约束”分开 |
| JSON 恢复 | 标准库 json.JSONDecoder 扫描 | 不执行代码，不引入宽松语法带来的安全歧义 |
| 评分点缩放 | Decimal 等比例缩放，最后一项补余数 | 保证总分不超过满分并避免二进制浮点累计误差 |
| API 兼容 | 保留路径与 envelope，仅新增可选诊断字段 | React 可渐进展示新能力，不需要整体重写 |
| 依赖锁定 | pyproject.toml + 精确版本 requirements.lock | 本机和新服务器可复现安装，兼容普通 pip |
| 切换策略 | 备份后停旧服务，迁移副本验收，再让 Python 成为唯一写入者 | 避免 SQLite 双写和不可恢复的数据分叉 |

## 错误与安全设计

- `AppError(status, code, message, fields)` 映射现有错误 envelope。
- Pydantic 请求错误统一为 `VALIDATION_ERROR`，字段路径转成前端可用格式。
- 百炼错误区分未配置、认证、限流、超时、网络、非 JSON、空响应和结构无效。
- 后台异常总是在数据库结束相应运行，再更新任务/草稿/提交状态。
- 日志 formatter 自动屏蔽 `Authorization`、`DASHSCOPE_API_KEY`、Base64 data URL 和超长模型内容。
- 文件预览先按 file ID 查询相对路径，再用 `Path.resolve()` 校验仍位于数据根目录。
- 搜索来源只允许 `http` 和 `https`，标题与摘要作为纯文本返回。

## 测试策略

### Python 单元测试

- JSON 候选提取与所有内容形态。
- 字段别名、题型映射、布尔/数字转换。
- agent_search 清空提前答案与超额评分点。
- reference_upload 评分点归一化与 Decimal 边界。
- 局部失败、重复题号、阻塞问题和单次结构修复。
- 路径安全、文件签名、PDF 页数及资源关闭。
- 百炼超时、认证、429 有限重试、搜索来源可靠性。

### Python 集成测试

- v2 数据库迁移、幂等、故障回滚和报告对比。
- 全 API contract 与现有响应快照。
- 参考答案、搜索命中、生成回退、单题失败、V1/V2 追溯。
- 学生初评、教师修改、确认、重开、报告与统计。
- 后台并发峰值和服务重启恢复。

### 前端测试

- 保留 TypeScript 类型检查和现有 React Testing Library 测试。
- 新增解析问题、归一化记录、结构修复运行和发布阻塞的 UI 测试。
- 1280px 视觉检查覆盖创建、答案审核、上传、复核和报告。

### 真实材料验证

- 把本次真实响应制作成脱敏固定夹具，保留字段类型、题目数量和第 5–8 题超分结构。
- 用本次 7 页 PDF 在 Python 文件处理器中验证 1–7 页和首尾页渲染。
- 真实百炼调用仍为显式付费验收，不进入默认测试。

## 切换与回滚

1. 停止旧 Express 服务，确认没有 Node 后台任务。
2. 备份整个 `data/`，记录 SQLite 文件大小、表计数和哈希。
3. 在备份副本上运行 Python v3 迁移、API contract 和报告对比。
4. 构建 React，启动 Python 生产服务执行健康、静态页面和关键流程冒烟。
5. 停止验证服务，在正式数据库上执行一次迁移并启动 Python 服务。
6. 切换后不再启动旧 Express 写服务。
7. 若迁移或冒烟失败，停止 Python，恢复完整 `data/` 备份后重新启动旧版本；不在同一数据库上逆向修改 v3。

## Spec 覆盖

| Spec 范围 | 设计归属 |
| --- | --- |
| PF1–PF5 | FastAPI、API、文件处理和百炼客户端 |
| PF6–PF15 | JSON 提取、逐题归一化、结构修复和运行持久化 |
| PF16–PF20 | 编排器、发布、学生批改、审计和启动恢复 |
| PN1–PN7 | 单机架构、SQLite 事务、Windows、后台队列和有限重试 |
| PN8–PN14 | 安全解析、日志脱敏、路径保护、幂等与大字段读取 |
| PN15–PN20 | 性能边界、关闭流程、测试隔离、依赖锁定和切换策略 |
| PAC1–PAC22 | Python/React 自动化测试、迁移对比、真实 PDF 和受控真机验收 |
