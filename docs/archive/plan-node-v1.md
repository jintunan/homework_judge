# 双科目作业批改 Agent MVP Plan（Node/TypeScript v1 归档）

## 架构概览

系统继续采用 React、Express 与 SQLite 的单机前后端一体架构，在现有学生批改链路之前增加“答案配置流水线”。

```text
教师上传试卷
   │
   ├─ 有参考答案 → 试卷识别 + 参考答案识别与题号匹配
   │
   └─ 无参考答案 → 试卷识别 → 百炼联网搜索
                                  │
                                  └─ 无可靠结果 → 模型独立解题
   │
   ▼
答案配置草稿
   │
   ▼
教师逐题审核、修改、重搜或重生成
   │
   ▼
发布为审核通过的答案版本
   │
   ▼
现有学生试卷批改流程
```

主要组件：

1. 任务与文件入口：选择科目和答案模式，保存模板试卷及可选参考答案。
2. 答案配置编排器：管理识别、搜索、生成、失败重试和整体进度。
3. 视觉题目提取器：提取题号、题干、题型、满分，以及可选参考答案。
4. 答案解析引擎：参考答案匹配；或搜索优先、模型生成回退。
5. 草稿与版本仓储：隔离未审核草稿和正式评分题目。
6. 教师答案审核工作台：逐题编辑、通过、退回、重搜和重生成。
7. 批改准入层：只允许当前已批准答案版本进入学生批改。
8. 现有学生批改与报告：扩展到高中物理并绑定答案版本。

## 技术栈

| 层级 | 选择 | 用途 |
| --- | --- | --- |
| Web 前端 | React + TypeScript + Vite | 创建任务、答案审核与现有四个工作区 |
| 路由与数据 | React Router + TanStack Query | 路由、轮询、缓存与状态刷新 |
| 样式 | 原生 CSS 视觉系统 | 延续现有桌面端界面 |
| 服务端 | Express + TypeScript | API、静态文件与后台编排 |
| 数据库 | Node `node:sqlite` | 版本、草稿、来源、模型与教师结果 |
| 上传 | Multer | 模板、参考答案与学生试卷 |
| 图像处理 | Sharp + PDF.js + Canvas | 图片标准化与 PDF 逐页栅格化 |
| 视觉模型 | 百炼 OpenAI-compatible Chat Completions | 试卷提取与学生试卷批改 |
| 联网搜索 | 百炼原生 Generation API | 获取 `search_info` 和来源 |
| 测试 | Vitest + Testing Library + Supertest | 单元、组件与 API 集成测试 |

## 核心数据结构

### GradingTask

- `id`、`name`、`className`、`paperName`
- `subject`：`middle_school_math` 或 `high_school_physics`
- `answerMode`：`reference_upload` 或 `agent_search`
- `templateFileId`
- `referenceAnswerFileId`：可空
- `answerConfigStatus`：`not_started`、`queued`、`extracting`、`searching`、`generating`、`review_pending`、`approved`、`failed`
- `activeAnswerVersionId`：当前正式答案版本
- `status`、`createdAt`、`updatedAt`

### AnswerConfigVersion

- `id`、`taskId`、`versionNumber`
- `status`：`draft`、`review_pending`、`approved`、`superseded`
- `answerMode`
- `createdAt`
- `approvedBy`、`approvedAt`

版本不可变；新配置从已批准版本派生草稿，不覆盖旧版本。

### AnswerQuestionDraft

- `id`、`versionId`
- `number`、`questionText`
- `type`：`choice`、`fill_blank`、`short_answer`、`calculation`
- `maxScore`
- `autoAnswer`、`autoScoringPoints`、`autoReason`
- `sourceType`：`reference_extracted`、`web_searched`、`model_generated`
- `confidence`、`needsAttention`
- `teacherAnswer`、`teacherScoringPoints`、`teacherMaxScore`、`teacherType`
- `reviewStatus`：`pending`、`approved`、`rejected`、`failed`
- `updatedBy`、`updatedAt`

自动字段和教师字段分开保存，教师字段优先形成当前审核结果。

### AnswerResolutionRun

- `id`、`taskId`、`versionId`
- `draftQuestionId`：整卷提取时可空
- `kind`：`exam_extraction`、`reference_extraction`、`web_search`、`model_generation`
- `provider`、`model`
- `requestSnapshot`、`rawResponse`、`parsedOutput`、`usage`
- `status`：`running`、`succeeded`、`parse_failed`、`request_failed`
- `errorCode`、`errorMessage`
- `startedAt`、`finishedAt`

### SearchSource

- `id`、`runId`、`draftQuestionId`
- `title`、`url`、`snippet`
- `rank`、`retrievedAt`

只保存有限长度摘要，不复制完整网页。

### Question

现有正式题目增加：

- `answerVersionId`
- `questionText`
- `sourceDraftId`

发布时从教师已审核草稿复制为不可变正式题目。

### Submission

增加 `answerVersionId`，在创建学生提交时固定绑定当前正式版本。

### StoredFile

`kind` 扩展为 `template`、`reference_answer`、`submission`。

### SubjectProfile

```ts
interface SubjectProfile {
  subject: "middle_school_math" | "high_school_physics";
  supportedTypes: QuestionType[];
  extractionInstructions: string;
  answerInstructions: string;
  scoringPointRules: string[];
}
```

初中数学支持选择、填空和简单简答；高中物理支持选择、填空和计算题。

## SQLite 迁移

使用 `PRAGMA user_version` 运行顺序迁移：

1. 重建需要扩展 CHECK 约束的表。
2. 新增答案配置版本、草稿、运行和来源表。
3. 为现有初中数学任务创建已批准的初始答案版本。
4. 把现有题目绑定到初始版本。
5. 把现有学生提交绑定到对应版本。
6. 保留全部上传文件、模型运行、教师复核和报告数据。

迁移在事务内执行；失败时回滚并阻止服务启动。

## 服务端接口

### 任务与生成

- `POST /api/tasks`：multipart 创建任务，接收科目、答案模式、模板和可选参考答案。
- `GET /api/tasks/:taskId`：返回任务、答案配置状态和当前版本。
- `POST /api/tasks/:taskId/answer-config-runs`：创建草稿版本并启动后台处理。
- `GET /api/tasks/:taskId/answer-config`：返回草稿、来源和运行摘要。
- `GET /api/tasks/:taskId/answer-config-progress`：返回生成与审核进度。

### 教师审核

- `PATCH /api/answer-drafts/:draftId`：保存教师修改。
- `POST /api/answer-drafts/:draftId/approve`：审核通过单题。
- `POST /api/answer-drafts/:draftId/reject`：退回单题。
- `POST /api/answer-drafts/:draftId/research`：重新搜索单题。
- `POST /api/answer-drafts/:draftId/regenerate`：直接重新生成单题。
- `POST /api/tasks/:taskId/answer-config/approve`：发布完整答案版本。
- `GET /api/answer-runs/:runId`：查看只读运行详情。

### 学生批改调整

- 上传学生试卷和启动批改均检查 `activeAnswerVersionId`。
- 学生提交写入 `answerVersionId`。
- 批改读取提交绑定版本的正式题目。
- 报告返回所用答案版本。
- 现有复核、报告和统计接口保持兼容。

## 模块设计

### AnswerConfigOrchestrator

负责创建草稿版本、调用提取器、选择答案路径、逐题有限并发处理、错误隔离、状态刷新、单题重试和重启恢复。

### VisionQuestionExtractor

复用现有文件预处理，将试卷和可选参考答案转换为有序页面图像；调用视觉模型并返回结构化题目。原始响应先保存，再通过运行时模式校验。

### DashScopeNativeSearchClient

使用百炼原生文本生成端点，配置 `enable_search: true` 和 `enable_source: true`。请求仅包含科目和单题公开题干。依据 `search_info` 提取来源；没有来源、没有直接答案或置信度不足时返回 `not_found`。

新增配置：

```dotenv
DASHSCOPE_NATIVE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_SEARCH_MODEL=qwen-plus
ANSWER_CONFIG_CONCURRENCY=2
ANSWER_SEARCH_CONFIDENCE_THRESHOLD=0.72
```

### ModelAnswerGenerator

对单题独立解题，返回标准答案、评分点、简要依据和置信度。初中数学生成关键步骤；高中物理生成公式、代入、单位和分步分值。不保存隐藏思维过程。

### AnswerConfigPublisher

在同一 SQLite 事务中校验所有草稿、生成正式题目、批准版本、切换活动版本并写入审计事件。

### SchemaMigrationRunner

读取 `PRAGMA user_version`，按顺序执行迁移并提供测试数据库升级入口。

### 批改准入层

由服务端统一检查答案配置是否已批准；页面禁用只用于即时反馈。

## 模块交互

### 上传参考答案

```text
创建任务
  → 保存试卷与参考答案
  → 创建答案版本
  → 转换页面图像
  → 视觉模型提取题目、答案和评分点
  → 按题号匹配与校验
  → 保存草稿和原始记录
  → 标记异常
  → 教师审核
```

参考答案中无法匹配的内容不自动绑定；缺失题可由教师修改、重搜或生成。

### 没有参考答案

```text
创建任务
  → 保存试卷
  → 视觉模型提取题目
  → 创建逐题草稿
  → 有限并发联网搜索
      ├─ 有可靠来源 → 保存搜索答案和来源
      └─ 无可靠来源 → 模型独立解题
  → 保存原始记录
  → 教师审核
```

每题独立处理，一题失败不阻塞其他题目。

### 教师审核与发布

```text
查看原卷与答案草稿
  → 修改 / 通过 / 退回 / 重搜 / 重生成
  → 所有题目审核通过
  → 发布不可变答案版本
  → 允许学生试卷批改
```

### 修改已发布答案

从当前版本派生新草稿，暂停新上传和新批改。已存在提交继续绑定旧版本，不重新计算或丢失报告。

### 学生批改

学生提交绑定当前答案版本，视觉模型按相应科目评分；教师终审后进入学生报告和班级统计。高中物理计算题逐项检查公式、代入、结果和单位。

### 错误与恢复

- 外部调用前创建运行记录。
- 原始响应先保存再解析。
- 重启时把遗留处理中状态恢复为可重试失败。
- 单题重试创建新运行，不覆盖历史。
- 发布和活动版本切换使用同一事务。
- 上传与启动批改分别执行服务端准入检查。

## 页面设计

### 创建任务与答案配置

五步流程：

1. 任务信息和科目。
2. 上传固定模板试卷。
3. 选择答案方式并可选上传参考答案。
4. Agent 自动识别、搜索或生成。
5. 教师审核并发布。

进度区显示状态、识别题数、搜索成功数、模型生成数、需关注数和失败数。

### 答案审核工作台

- 左侧为试卷原页，支持翻页、缩放和适宽。
- 右侧为逐题草稿。
- 筛选：全部、待审核、需关注、检索答案、模型生成、失败。
- 展示题号、题干、题型、满分、答案、评分点、来源、置信度、搜索证据和运行历史。
- 操作：保存、通过、退回、重搜、重生成。
- 底部固定显示审核进度和发布按钮。

### 批量上传

未发布答案时显示阻塞原因并禁止学生文件上传与启动批改；发布后显示当前答案版本和批准时间。

### 学生复核与报告

显示科目和答案版本；高中物理计算题突出公式、步骤和单位评分点。旧成绩不因新版本发布而改变。

## 文件组织

```text
homework_judge/
├─ shared/
│  ├─ contracts.ts
│  ├─ schemas.ts
│  └─ subject-profiles.ts
├─ server/src/
│  ├─ answer-config/
│  │  ├─ orchestrator.ts
│  │  ├─ extractor.ts
│  │  ├─ resolver.ts
│  │  ├─ publisher.ts
│  │  ├─ prompts.ts
│  │  └─ output.ts
│  ├─ model/
│  │  ├─ dashscope.ts
│  │  ├─ dashscope-search.ts
│  │  └─ answer-generator.ts
│  ├─ db/
│  │  ├─ migrations.ts
│  │  ├─ migrations/002-answer-config.sql
│  │  └─ repositories/
│  │     ├─ answer-config.ts
│  │     └─ answer-runs.ts
│  └─ api/
│     ├─ tasks.ts
│     └─ answer-config.ts
├─ client/src/
│  ├─ features/tasks/CreateTaskPage.tsx
│  ├─ features/answer-config/
│  │  ├─ AnswerConfigPage.tsx
│  │  ├─ AnswerProgress.tsx
│  │  ├─ AnswerDraftCard.tsx
│  │  ├─ SourceEvidence.tsx
│  │  └─ RunHistoryDrawer.tsx
│  ├─ features/submissions/UploadPage.tsx
│  ├─ features/review/ReviewPage.tsx
│  ├─ lib/api.ts
│  └─ styles.css
└─ tests/
   ├─ answer-config/
   ├─ api/answer-config-workflow.test.ts
   ├─ model/dashscope-search.test.ts
   └─ ui/answer-config.test.tsx
```

## 关键技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 搜索接口 | 百炼原生 Generation API | 能判断实际搜索并保存来源 |
| 搜索模型 | 独立环境变量 | 不与视觉能力、价格和接口形态绑定 |
| 处理粒度 | 整卷识别、逐题解析 | 保留版面关系并隔离单题失败 |
| 草稿与正式题目 | 分表保存 | 未审核内容不会被批改读取 |
| 答案历史 | 不可变版本 | 已批改试卷可持续追溯 |
| 搜索成功 | 来源、直接答案、置信度同时满足 | 无来源内容不能冒充检索答案 |
| 搜索失败 | 自动模型生成 | 形成连续可审核流程 |
| 审核方式 | 逐题通过、整卷发布 | 兼顾定位风险和版本完整性 |
| 修改已发布配置 | 派生新版本 | 不破坏旧提交和报告 |
| 科目规则 | SubjectProfile | 保持题型、提示词和校验一致 |
| 数据迁移 | `PRAGMA user_version` | 兼容现有 SQLite 数据 |
| 队列 | 单进程有限并发 | 符合单机 MVP |
| 证据保存 | 原始运行只读 | 满足审计和追溯 |
| 搜索隐私 | 只发送题干和科目 | 不暴露教师与学生信息 |

## 安全与错误处理

- API Key 不进入数据库、日志、请求快照或浏览器响应。
- 上传按白名单校验扩展名、媒体类型、文件头、大小和页数。
- 文件使用 UUID 保存，预览接口只接受数据库文件 ID。
- 来源 URL 只允许 `http` 和 `https`，文本按纯文本展示并限制长度。
- 外部请求设置超时、有限重试和并发上限。
- 所有自动输出由 Zod 校验，教师字段由服务端再次校验。
- 搜索不提交班级、教师、学生或学生答卷数据。
- 原始自动结果不可编辑；教师结果使用独立字段。
- 版本发布、题目复制、状态切换和审计写入使用同一事务。

## Spec 覆盖关系

- F1–F5：任务入口、文件服务、视觉提取器。
- F6–F8：百炼原生搜索、答案解析和模型生成。
- F9–F10：草稿、运行记录和来源仓储。
- F11–F13：答案审核工作台和单题操作接口。
- F14–F15：发布器、答案版本和服务端准入。
- F16：科目规则和专用提示词。
- F17–F19：现有学生批改、报告与统计。
- F20：创建流程和页面导航。
- F21：服务端模型适配器与去敏记录。
- N1–N15：迁移、有限并发、恢复、输入校验、审计、安全和可见状态均有模块归属。
