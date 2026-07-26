# Python 后端迁移与试卷结构容错解析 Tasks

## 实施状态（2026-07-26）

- T1–T38 的功能实现与旧 Express 清理已完成；
- T39 的核心无计费验收已完成：Lint、类型检查、测试、构建、正式库迁移、生产冒烟、真实 7 页 PDF 和 1280px 页面检查；
- 教师业务验收、真实百炼计费场景及 `checklist.md` 中尚未勾选的强化/故障注入项目继续保留，不以代码完成代替教师审批。

## 文件清单

| 操作 | 文件或目录 | 职责 |
| --- | --- | --- |
| 新建 | `pyproject.toml`、`requirements.lock` | Python 项目、精确依赖、pytest、mypy、ruff 配置 |
| 新建 | `backend/homework_judge/config.py` | 环境变量、路径、阈值和并发配置 |
| 新建 | `backend/homework_judge/errors.py`、`schemas.py`、`subjects.py` | 错误、API/领域模型和双科目规则 |
| 新建 | `backend/homework_judge/db/*` | SQLite 连接、最新 schema、v3 迁移和仓储 |
| 新建 | `backend/homework_judge/files/*` | 上传存储、文件签名和 PDF/图像处理 |
| 新建 | `backend/homework_judge/model/*` | 百炼视觉、原生搜索和答案生成客户端 |
| 新建 | `backend/homework_judge/answer_config/*` | 提示词、容错解析、归一化、修复、编排和发布 |
| 新建 | `backend/homework_judge/grading/*` | 学生试卷识别、模型初评和批改编排 |
| 新建 | `backend/homework_judge/jobs/manager.py` | 有界后台队列、worker、去重和关闭 |
| 新建 | `backend/homework_judge/reports/statistics.py` | 学生报告和班级统计 |
| 新建 | `backend/homework_judge/api/*` | 与现有 React 兼容的 FastAPI 路由 |
| 新建 | `backend/homework_judge/main.py`、`run.py` | ASGI 应用、lifespan、静态页面和生产入口 |
| 新建 | `backend/tests/*` | Python 单元、集成、迁移、安全和端到端测试 |
| 修改 | `shared/contracts.ts` | 可选解析诊断、归一化记录和结构修复运行类型 |
| 修改 | `client/src/features/answer-config/*` | 展示解析问题、调整记录和重新识别入口 |
| 修改 | `package.json` | Python API、前端、测试和生产统一命令 |
| 修改 | `.env.example`、`.gitignore`、`README.md` | Python 环境、启动、备份、切换和回滚 |
| 删除 | `server/`、旧服务端测试、`tsconfig.server.json` | 兼容验收后移除 Express 运行时 |
| 保留 | `client/`、`shared/`、React UI 测试、`data/` | 前端、前端契约、正式数据和上传文件 |

## T1：建立 Python 项目与锁定依赖

**文件：** `pyproject.toml`、`requirements.lock`、`backend/homework_judge/__init__.py`

**依赖：** 无

**步骤：**

1. 配置 Python 3.12、FastAPI、Uvicorn、Pydantic Settings、HTTPX、aiosqlite、python-multipart、Pillow 和 pypdfium2。
2. 配置 pytest、pytest-asyncio、mypy 和 ruff，并固定全部直接依赖版本。
3. 建立可导入的 `homework_judge` 包和测试路径。

**验证：** 新建隔离虚拟环境安装 `requirements.lock`，运行 `python -c "import fastapi, aiosqlite, pypdfium2"` 成功。

## T2：实现配置加载与启动前校验

**文件：** `backend/homework_judge/config.py`

**依赖：** T1

**步骤：**

1. 移植 `.env.example` 中全部配置和默认值。
2. 校验端口、页数、上传大小、并发、阈值、超时和 URL。
3. 解析数据、数据库、上传和临时目录为绝对路径。
4. 为 API Key 使用秘密类型，并提供不含 Key 的模型状态信息。

**验证：** 单元测试覆盖默认配置、环境覆盖、非法数字、非法 URL 和脱敏序列化。

## T3：定义领域模型、科目规则与统一错误

**文件：** `backend/homework_judge/schemas.py`、`subjects.py`、`errors.py`

**依赖：** T1

**步骤：**

1. 把 `shared/contracts.ts` 中服务端需要的枚举和 DTO 定义为 Pydantic 模型。
2. 定义初中数学与高中物理支持题型、提示词规则和兜底题型。
3. 定义 `AppError`、模型请求错误和 API 错误 envelope。
4. 为 snake_case 数据库字段与 camelCase API 字段建立显式 alias。

**验证：** mypy 通过；模型往返序列化与现有前端响应快照一致。

## T4：实现 SQLite 连接与事务基础

**文件：** `backend/homework_judge/db/database.py`

**依赖：** T2

**步骤：**

1. 创建 aiosqlite 连接工厂并统一设置 foreign keys、WAL、busy timeout 和 row factory。
2. 提供只读查询、写入和 `BEGIN IMMEDIATE` 事务上下文。
3. 实现 JSON、时间和 Decimal 的稳定编解码。
4. 确保异常和取消时连接、游标及事务正确关闭或回滚。

**验证：** 并发读、事务提交、异常回滚、外键失败和连接关闭测试通过。

## T5：建立最新 schema v3

**文件：** `backend/homework_judge/db/schema.sql`

**依赖：** T3、T4

**步骤：**

1. 按现有 v2 表、约束、索引和外键重建完整最新 schema。
2. 为答案版本加入 extraction issues 和 unresolved count。
3. 为答案草稿加入 parse issues、normalizations 和 requires correction。
4. 为答案运行加入 `structure_repair` 类型。

**验证：** 空数据库初始化后 `user_version=3`，表、列、索引和 `foreign_key_check` 全部正确。

## T6：实现 v2→v3 幂等迁移

**文件：** `backend/homework_judge/db/migrations.py`、`db/migrations/003-python-parser.sql`

**依赖：** T4、T5

**步骤：**

1. 检测 user_version、表和列，避免重复 ALTER。
2. 在一个事务中增加版本/草稿字段并重建答案运行表。
3. 保持所有旧行 ID、JSON、时间、状态及外键。
4. 迁移后执行外键检查，失败时回滚并停止启动。

**验证：** v2 副本迁移、连续迁移两次、注入复制失败和迁移前后表计数测试通过。

## T7：移植文件、任务与审计仓储

**文件：** `backend/homework_judge/db/repositories/files.py`、`tasks.py`、`audit.py`

**依赖：** T3–T6

**步骤：**

1. 移植文件记录、任务创建/查询/修改、模板与参考答案关联。
2. 移植兼容题目保存接口及不可变答案版本创建。
3. 移植任务进度、总分和活动答案版本聚合。
4. 移植审计写入和学生审计查询。

**验证：** 使用现有 v2 数据快照对比 Node 响应和 Python 响应。

## T8：移植答案配置仓储

**文件：** `backend/homework_judge/db/repositories/answer_config.py`、`answer_runs.py`

**依赖：** T6、T7

**步骤：**

1. 实现版本创建、查询、supersede、草稿替换和进度统计。
2. 映射原始/教师/有效题号、题型、满分、答案和评分点。
3. 保存 parse issues、normalizations、requires correction 和版本级 issues。
4. 实现运行开始、成功、失败、来源保存和只读历史查询。

**验证：** 版本、草稿、运行和来源 CRUD 测试覆盖全部状态与 JSON 字段。

## T9：移植提交、模型运行与教师复核仓储

**文件：** `backend/homework_judge/db/repositories/submissions.py`、`model_runs.py`、`reviews.py`

**依赖：** T6、T7

**步骤：**

1. 实现学生提交创建、答案版本绑定、姓名更新和状态流转。
2. 实现学生模型运行原始响应和解析结果持久化。
3. 实现逐题初评、教师修改、撤销确认和整卷确认事务。
4. 保持已有答案版本只读和服务端准入规则。

**验证：** 迁移前确认报告、教师改分、重开和重新确认测试通过。

## T10：移植学生报告与班级统计

**文件：** `backend/homework_judge/reports/statistics.py`

**依赖：** T7、T9

**步骤：**

1. 实现学生报告、是否最终、总分和逐题明细。
2. 实现按答案版本分组的学生数量和确认数量。
3. 实现平均/最高/最低、成绩段和逐题得分率。
4. 只聚合教师确认结果，并使用提交绑定版本。

**验证：** 对同一 SQLite 副本比较 Python 与旧版报告/统计 JSON。

## T11：实现安全文件上传与本地存储

**文件：** `backend/homework_judge/files/storage.py`

**依赖：** T2、T3

**步骤：**

1. 实现任务双文件和学生批量文件的 multipart 限制。
2. 校验扩展名、声明 MIME、文件签名、大小和允许类型。
3. 使用 UUID 存储名和相对路径，失败时删除临时/已持久化文件。
4. 文件预览按登记记录解析路径并阻止越界。

**验证：** 合法 PDF/JPG/PNG、伪装扩展名、损坏文件、超限、路径穿越和混合批次测试通过。

## T12：实现 Python PDF 与图像处理

**文件：** `backend/homework_judge/files/processor.py`

**依赖：** T1、T2、T11

**步骤：**

1. 用 Pillow 完成方向纠正、RGB 转换、尺寸限制和 JPEG 编码。
2. 用 pypdfium2 校验 PDF 页数并逐页渲染。
3. 每页完成后关闭 bitmap/page，最终关闭 document 和文件句柄。
4. 输出连续页码、JPEG data URL 和 byte length。

**验证：** 最小 PDF、损坏 PDF、超页 PDF和本次 7 页真实 PDF 测试通过，并检查首尾页渲染。

## T13：实现百炼异步客户端基础

**文件：** `backend/homework_judge/model/dashscope.py`

**依赖：** T2、T3

**步骤：**

1. 建立共享 HTTPX AsyncClient、认证头和兼容接口请求。
2. 实现字符串/文本块数组的 message content 提取。
3. 实现超时、认证、429、5xx、网络错误和有限重试分类。
4. 构造不含 API Key 与 Base64 的请求快照和模型状态。

**验证：** MockTransport 覆盖成功、空响应、数组内容、401、429 三次、500、超时和断网。

## T14：实现原生联网搜索客户端

**文件：** `backend/homework_judge/model/dashscope_search.py`

**依赖：** T13

**步骤：**

1. 移植 `enable_search`、`enable_source` 和 `forced_search` 请求。
2. 提取直接答案、评分点、置信度和 search sources。
3. 只保留 HTTP(S) 来源并截断标题/摘要。
4. 仅在答案、来源和阈值同时满足时标记可靠命中。

**验证：** 命中、无来源、低置信度、恶意 URL、429 和隐私请求快照测试通过。

## T15：实现答案生成客户端

**文件：** `backend/homework_judge/model/answer_generator.py`

**依赖：** T13

**步骤：**

1. 移植双科目单题生成提示词和 JSON 输出。
2. 使用共享安全 JSON 提取器解析生成结果。
3. 校验答案、评分点、原因和置信度。
4. 保存原始响应和 Token 用量。

**验证：** 数学简答、物理计算、无效 JSON、超分评分点和请求失败测试通过。

## T16：实现安全 JSON 候选提取

**文件：** `backend/homework_judge/answer_config/parser.py`

**依赖：** T3

**步骤：**

1. 支持完整 JSON、Markdown fence、前后文本和数组根节点。
2. 使用 JSONDecoder 扫描候选位置并按题目结构选择结果。
3. 支持 `questions` 和约定中文根字段。
4. 返回候选形态与提取诊断，禁止任何代码求值。

**验证：** PAC7 候选夹具、嵌套括号、字符串内括号、多候选、截断和恶意表达式测试通过。

## T17：实现字段适配与基础逐题归一化

**文件：** `backend/homework_judge/answer_config/normalizer.py`

**依赖：** T3、T16

**步骤：**

1. 映射题号、题干、题型、满分、答案、评分点、原因和置信度别名。
2. 转换数字字符串、布尔字符串、题型别名和置信度。
3. 为缺失/重复题号生成唯一待核号并设置 requires correction。
4. 隔离非对象、缺题干和非法满分节点并记录路径。

**验证：** 合法/可恢复/不可恢复混合响应产生预期 questions 与 issues。

## T18：实现答案模式与评分点归一化

**文件：** `backend/homework_judge/answer_config/normalizer.py`

**依赖：** T17

**步骤：**

1. agent_search 在最终评分点校验前清空提前答案和评分点。
2. reference_upload 丢弃非法评分点并记录原值。
3. 使用 Decimal 等比例缩放超额合法评分点，最后一项补余数。
4. 写入 needs attention、normalizations 和可展示原因。

**验证：** 本次第 5–8 题超分夹具不再整卷失败；参考答案超分合计精确不超过满分。

## T19：实现视觉提取与一次结构修复

**文件：** `backend/homework_judge/answer_config/extractor.py`、`prompts.py`

**依赖：** T8、T12、T13、T16–T18

**步骤：**

1. 读取模板/参考答案文件并调用视觉客户端。
2. 保存识别运行原始响应后执行本地解析。
3. 无可用题目时创建且只创建一次 structure repair 运行。
4. 保存修复结果、issues、normalizations 和版本 unresolved count。
5. 错误中返回运行 ID、路径和截断摘要。

**验证：** 首次成功、局部成功、修复成功、修复失败和隐私快照测试通过。

## T20：实现搜索优先与生成回退

**文件：** `backend/homework_judge/answer_config/resolver.py`

**依赖：** T8、T14、T15

**步骤：**

1. 每题先创建搜索运行并保存来源。
2. 可靠命中写入 web searched 草稿。
3. not found 调用生成并写入 model generated 草稿。
4. 请求异常和双重失败只影响当前题。

**验证：** 命中、回退、搜索异常、生成异常、单题隔离和历史保留测试通过。

## T21：实现有界后台任务管理器

**文件：** `backend/homework_judge/jobs/manager.py`

**依赖：** T4

**步骤：**

1. 建立答案配置和学生批改有界队列。
2. 按配置启动固定 worker 与 semaphore。
3. 用 JobKey 阻止同一任务/草稿/提交重复入队。
4. 关闭时停止接收、等待限时并标记中断运行。

**验证：** 峰值并发、队列满、重复任务、单任务异常和 shutdown 测试通过。

## T22：实现答案配置编排器

**文件：** `backend/homework_judge/answer_config/orchestrator.py`

**依赖：** T8、T19–T21

**步骤：**

1. 首次启动创建答案版本并入队识别。
2. 局部成功后创建草稿并按模式启动 resolver。
3. 失败/有阻塞问题的整卷重试创建新版本并 supersede 旧草稿版本。
4. 单题 research/regenerate 保持历史并刷新任务状态。

**验证：** 状态序列、重试版本、并发限制和服务恢复测试通过。

## T23：实现教师审核与事务发布

**文件：** `backend/homework_judge/answer_config/publisher.py`

**依赖：** T8、T22

**步骤：**

1. 移植教师修改、通过、退回和修订规则。
2. 教师提交完整有效修改后清除 requires correction。
3. 发布前检查 unresolved issues、requires correction、全部审核状态和评分点。
4. 一个事务写入正式题目、批准版本、切换活动版本和审计。

**验证：** 阻塞项、重复题号、超分、只读版本、V1/V2 和注入发布失败回滚测试通过。

## T24：移植学生评分提示词与输出解析

**文件：** `backend/homework_judge/grading/prompt.py`、`output.py`

**依赖：** T3、T13、T16

**步骤：**

1. 移植双科目评分提示词与请求快照。
2. 使用共享 JSON 候选提取处理模型初评输出。
3. 校验题号覆盖、重复、分数范围和置信度。
4. 低置信度和非法分数标记 needs attention。

**验证：** 数学/物理三题型、缺题、未知题号、重复、越界和 fenced JSON 测试通过。

## T25：实现学生批改编排器

**文件：** `backend/homework_judge/grading/orchestrator.py`

**依赖：** T9、T12、T13、T21、T24

**步骤：**

1. 服务端检查答案版本批准和提交绑定。
2. 逐提交处理文件、百炼初评和模型运行。
3. 保存逐题初评与总分，进入教师复核。
4. 单提交失败隔离、有限重试和重启恢复。

**验证：** 批量并发、旧版本绑定、解析失败、模型失败和重试测试通过。

## T26：实现 FastAPI 基础、错误层和静态页面

**文件：** `backend/homework_judge/main.py`、`run.py`、`api/response.py`

**依赖：** T2–T6、T21

**步骤：**

1. lifespan 中迁移、恢复、创建 HTTP client 和启动 worker。
2. 注册 JSON 大小、CORS/同源和统一异常处理。
3. 生产环境挂载构建资源及 React catch-all。
4. 实现开发/生产入口和优雅关闭。

**验证：** `/api/health`、未知 API、React 根页、子路由刷新和 shutdown 冒烟通过。

## T27：实现健康、任务与文件 API

**文件：** `backend/homework_judge/api/health.py`、`tasks.py`、`files.py`

**依赖：** T7、T11、T26

**步骤：**

1. 实现健康和脱敏模型状态。
2. 实现任务列表、创建、详情、修改和兼容题目保存。
3. 实现模板/参考答案 multipart 规则和失败清理。
4. 实现登记文件预览和 Content-Type。

**验证：** 与现有 React contract 快照一致，上传和路径安全测试通过。

## T28：实现答案配置 API

**文件：** `backend/homework_judge/api/answer_config.py`

**依赖：** T22、T23、T26

**步骤：**

1. 实现启动、详情、进度、运行详情。
2. 实现草稿修改、通过、退回、research、regenerate。
3. 实现发布和修订。
4. 在详情中返回可选解析问题、归一化和 requires correction。

**验证：** 所有现有路由、状态码、envelope 和新增诊断字段集成测试通过。

## T29：实现提交、批改、复核与报告 API

**文件：** `backend/homework_judge/api/submissions.py`、`grading.py`、`reviews.py`、`reports.py`

**依赖：** T9、T10、T25、T26

**步骤：**

1. 实现提交列表、批量上传、学生姓名修改和进度。
2. 实现启动批改和单提交重试。
3. 实现复核详情、逐题教师修改、确认和审计。
4. 实现学生报告和班级统计。

**验证：** 完整教师批改 workflow 与旧版 API 快照一致。

## T30：实现启动恢复与状态修复

**文件：** `backend/homework_judge/db/recovery.py`

**依赖：** T8、T9、T21

**步骤：**

1. 把运行中的答案识别、结构修复、搜索和生成标记为可重试失败。
2. 把 processing 学生提交和 running 模型运行标记为中断失败。
3. 更新受影响草稿和任务状态。
4. 保证连续执行恢复逻辑幂等。

**验证：** 五个中断阶段和重复启动测试通过。

## T31：更新 React 契约与答案审核诊断

**文件：** `shared/contracts.ts`、`client/src/lib/api.ts`、`client/src/features/answer-config/*`

**依赖：** T28

**步骤：**

1. 增加 structure repair、parse issues、normalizations 和 requires correction 可选类型。
2. 在运行历史区分原识别与结构修复。
3. 在题卡显示调整前后值、原因和必须修正提示。
4. 版本级阻塞问题显示重新识别入口；发布按钮与服务端规则一致。

**验证：** TypeScript 类型检查、组件测试和键盘操作测试通过。

## T32：建立真实失败脱敏夹具与解析测试

**文件：** `backend/tests/fixtures/answer-extraction-overflow.json`、`unit/test_parser.py`、`unit/test_normalizer.py`

**依赖：** T16–T18

**步骤：**

1. 从已保存运行脱敏题干，保留八题结构、字段类型和第 5–8 题超分关系。
2. 验证 agent_search 清除答案/评分点并保留全部题目。
3. 验证 reference_upload 归一化和关注标记。
4. 覆盖全部候选形态、字段别名和局部坏节点。

**验证：** pytest 输出所有 parser/normalizer 用例通过且夹具不含班级、教师、学生或 Key。

## T33：建立迁移与 API contract 测试

**文件：** `backend/tests/integration/test_migration.py`、`test_api_contract.py`

**依赖：** T6、T27–T30

**步骤：**

1. 制作 v2 数据库夹具并记录表计数、ID、报告和统计。
2. 验证 v3 迁移、幂等和故障回滚。
3. 用 FastAPI ASGI transport 覆盖全部公开路由。
4. 对关键成功和错误响应与前端 contract 做快照对比。

**验证：** 迁移前后数据和 API 对比测试全部通过。

## T34：建立答案配置端到端测试

**文件：** `backend/tests/integration/test_answer_config.py`

**依赖：** T19–T23、T28、T32

**步骤：**

1. 覆盖参考答案提取、教师修改、逐题通过和发布。
2. 覆盖无参考答案搜索命中与生成回退。
3. 覆盖局部失败、一次结构修复、整卷重试和历史保留。
4. 覆盖 V1/V2、上传准入和发布事务回滚。

**验证：** 使用假百炼完成所有场景且峰值并发不超配置。

## T35：建立学生批改与报告端到端测试

**文件：** `backend/tests/integration/test_grading.py`、`test_reports.py`

**依赖：** T24、T25、T29

**步骤：**

1. 覆盖双科目三题型初评。
2. 覆盖教师改分、批注、确认和确认后重开。
3. 覆盖旧答案版本报告和多版本班级统计。
4. 覆盖批量单题/单提交故障隔离。

**验证：** 最终分、统计值和答案版本全部与手工计算一致。

## T36：更新统一命令与开发流程

**文件：** `package.json`、`.env.example`、`.gitignore`

**依赖：** T26–T35

**步骤：**

1. `npm run dev` 并行启动 Python reload API 和 Vite。
2. `npm test` 依次运行 Python 测试与 React 测试。
3. `npm run typecheck` 运行 mypy 和 TypeScript；`npm run lint` 运行 ruff。
4. `npm run build` 构建 React 并执行 Python compileall，`npm start` 启动 Python 生产服务。

**验证：** 所有统一命令在 PowerShell 新终端成功退出或持续提供服务。

## T37：完成数据切换工具与文档

**文件：** `backend/homework_judge/tools/verify_migration.py`、`README.md`

**依赖：** T33、T36

**步骤：**

1. 提供只读表计数、外键、ID 和报告摘要对比工具。
2. 记录虚拟环境安装、开发、生产、备份、迁移和恢复。
3. 记录停止旧服务、禁止双写、Python 切换和回滚步骤。
4. 记录真实百炼显式验证与费用提醒。

**验证：** 在数据副本上按 README 从零完成安装、迁移验证和生产冒烟。

## T38：移除 Express 运行时并清理旧服务测试

**文件：** `server/`、`tests/api/*`、`tests/db/*`、`tests/files/*`、`tests/grading/*`、`tests/model/*`、`tsconfig.server.json`

**依赖：** T33–T37

**步骤：**

1. 确认 Python contract、迁移和端到端测试全部通过。
2. 删除 Express 服务端源码、仅服务旧后端的测试和服务端 TS 编译配置。
3. 从 package 依赖移除 Express、Multer、Sharp、PDF.js 服务端依赖及其类型。
4. 保留 React 所需依赖、shared contract 和 UI 测试。

**验证：** 搜索不到运行时 Express 入口；统一构建、测试和生产启动仍全部通过。

## T39：执行完整验收与 1280px 视觉检查

**文件：** `checklist.md`、视觉测试脚本

**依赖：** T1–T38

**步骤：**

1. 执行 Python lint、mypy、pytest、React typecheck、UI tests 和生产构建。
2. 在隔离 v2 数据副本执行迁移、生产健康/API/React 冒烟。
3. 用本次 7 页 PDF 运行 Python 文件处理器并检查首尾页。
4. 在 1280px 检查创建、答案审核、上传、复核、报告和错误诊断页面。
5. 扫描数据库、日志、API 和构建产物中的测试 Key。

**验证：** checklist 除显式付费真实百炼场景外全部勾选，无遗留服务和临时文件。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6
                 ├────────→ T7 → T8 → T9 → T10
                 ├────────→ T11 → T12
                 └────────→ T13 → T14 / T15
T3 → T16 → T17 → T18
T8 + T12–T18 → T19 → T20
T4 → T21
T8 + T19–T21 → T22 → T23
T9 + T12 + T13 + T16 → T24 → T25
T2–T6 + T21 → T26
T7 + T11 + T26 → T27
T22 + T23 + T26 → T28
T9 + T10 + T25 + T26 → T29
T8 + T9 + T21 → T30
T28 → T31
T16–T18 → T32
T6 + T27–T30 → T33
T19–T23 + T28 + T32 → T34
T24 + T25 + T29 → T35
T26–T35 → T36 → T37 → T38 → T39
```
