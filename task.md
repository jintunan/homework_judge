# 试卷题目识别与参考答案匹配 Tasks

## 文件清单

| 操作 | 文件/目录 | 职责 |
| --- | --- | --- |
| 新建 | `.env.example`、`.gitignore`、`README.md` | 配置说明、忽略运行数据、启动与验收说明 |
| 新建 | `pyproject.toml`、`requirements.lock` | Python 项目、质量工具和锁定依赖 |
| 新建 | `package.json`、`pnpm-lock.yaml`、TypeScript/Vite 配置 | 前端构建、测试和统一启动 |
| 新建 | `backend/homework_judge/config.py`、`errors.py`、`schemas.py` | 配置、错误和服务端契约 |
| 新建 | `backend/homework_judge/db/` | SQLite 连接、迁移、仓储和审计 |
| 新建 | `backend/homework_judge/files/` | 上传存储、文件校验和页面渲染 |
| 新建 | `backend/homework_judge/recognition/` | 百炼客户端、提示词、解析、归一化和识别服务 |
| 新建 | `backend/homework_judge/matching/` | 题号规范化、相似度和一对一匹配 |
| 新建 | `backend/homework_judge/jobs/` | 后台任务与完整流水线 |
| 新建 | `backend/homework_judge/api/`、`main.py`、`run_server.py` | HTTP API、生命周期和静态页面 |
| 新建 | `client/src/`、`shared/` | 上传、进度、审核、预览和共享类型 |
| 新建 | `backend/tests/`、`tests/ui/` | 单元、集成和 UI 测试 |
| 新建 | `scripts/clean-build.mjs`、`scripts/visual-check.mjs` | 可重复构建和桌面页面视觉检查 |

## T1：建立项目与依赖基线

**文件：** `pyproject.toml`、`requirements.lock`、`package.json`、`pnpm-lock.yaml`、`tsconfig.json`、`vite.config.ts`、`vitest.config.ts`
**依赖：** 无

**步骤：**
1. 固定 Python 3.12 和所有后端依赖版本。
2. 固定 React、Vite、TypeScript 和测试依赖版本并生成锁文件。
3. 配置前端 `/api` 开发代理、生产构建目录和 Python 测试路径。
4. 增加 `dev`、`build`、`start`、`typecheck`、`lint`、`test` 脚本。

**验证：** 安装锁定依赖后，运行 Python 导入检查、`npm run typecheck` 和空测试命令，均正常退出。

## T2：定义配置、错误和 API 响应契约

**文件：** `.env.example`、`backend/homework_judge/config.py`、`errors.py`、`schemas.py`、`shared/contracts.ts`、`shared/schemas.ts`
**依赖：** T1

**步骤：**
1. 定义 plan.md 中全部环境变量、默认值和合法范围。
2. 定义安全的应用错误、统一成功/失败响应和字段级错误详情。
3. 定义任务、文档、运行、题目、答案条目、匹配和审核的 Pydantic/TypeScript 契约。
4. 确保任何公开契约都不包含 API Key、绝对路径或图片 Data URL。

**验证：** 运行契约单元测试和前端类型检查，验证非法配置在启动时给出明确错误。

## T3：建立 SQLite 连接与迁移框架

**文件：** `backend/homework_judge/db/database.py`、`migrations.py`
**依赖：** T2

**步骤：**
1. 实现单连接初始化、短事务上下文、WAL 和外键配置。
2. 创建 schema_version 和幂等迁移执行器。
3. 建立 plan.md 所列任务、文档、页面、运行、题目、答案、匹配和审计表。
4. 为 task_id、status、normalized_number、更新时间和外键添加索引与约束。

**验证：** 在空数据库连续运行两次迁移，表结构不变，`foreign_key_check` 无错误。

## T4：实现仓储与审计写入

**文件：** `backend/homework_judge/db/repositories.py`
**依赖：** T3

**步骤：**
1. 实现任务、文件、运行、识别条目和匹配的创建与查询。
2. 实现人工覆盖、确认、解除确认和答案忽略的事务写入。
3. 实现完成准入查询和最近任务列表。
4. 每个变更操作在同一事务写入白名单审计事件。

**验证：** 仓储集成测试覆盖创建、更新、回滚、外键失败和审计 payload 脱敏。

## T5：实现安全文件存储

**文件：** `backend/homework_judge/files/storage.py`
**依赖：** T2

**步骤：**
1. 创建任务专属 uploads、pages、tmp 目录。
2. 流式保存文件并同时计算大小和 SHA-256。
3. 使用 UUID 磁盘名并返回数据根目录内的相对路径。
4. 实现原子提交、失败清理和安全路径解析。

**验证：** 单元测试证明同名上传不覆盖，路径穿越被拒绝，失败上传不留下正式文件。

## T6：实现上传文件类型校验

**文件：** `backend/homework_judge/files/validation.py`
**依赖：** T5

**步骤：**
1. 校验 PDF、DOCX、JPG、PNG 的扩展名和文件签名。
2. 用 Pillow 验证图片可解码，用 ZIP 结构验证 DOCX。
3. 拒绝空文件、伪装扩展名、加密/不可读 PDF 和超限文件。
4. 生成稳定的用户错误码。

**验证：** 文件夹具覆盖四种合法格式及伪 PDF、伪 DOCX、损坏图片、空文件和超限文件。

## T7：实现图片与 PDF 页面渲染

**文件：** `backend/homework_judge/files/renderer.py`
**依赖：** T6

**步骤：**
1. 使用 Pillow 处理图片方向、透明背景、颜色空间和尺寸。
2. 使用 pypdfium2 校验 PDF 页数并逐页渲染。
3. 把每页编码为受控尺寸和质量的 JPEG，并记录页码、尺寸和摘要。
4. 所有文档、页和位图资源在成功与异常路径都显式关闭。

**验证：** 对多页 PDF 和旋转/透明图片运行渲染测试，页码连续、图像可打开且没有资源占用错误。

## T8：实现 DOCX 转换与统一页面准备

**文件：** `backend/homework_judge/files/renderer.py`
**依赖：** T7

**步骤：**
1. 探测可用 LibreOffice/soffice 并使用隔离用户配置目录。
2. 在任务临时目录把 DOCX 转为 PDF。
3. 复用 PDF 渲染流程生成正式页面。
4. 在转换失败、超时和退出码异常时提供明确错误并清理临时文件。

**验证：** 渲染仓库中的至少一份含公式和示意图 DOCX，页数非零，每页图像可读；模拟缺少 LibreOffice 时返回预期错误。

## T9：实现百炼客户端

**文件：** `backend/homework_judge/recognition/client.py`
**依赖：** T2

**步骤：**
1. 实现 OpenAI 兼容多模态请求、JSON 响应提取和 Token 用量读取。
2. 支持 Workspace Base URL、模型、超时、并发和有限重试。
3. 仅对 429、5xx 和网络临时错误重试，认证和结构错误立即失败。
4. 生成不含密钥、授权头和图片数据的请求快照。

**验证：** 使用模拟 HTTP 服务覆盖成功、401、429 后成功、持续 500、超时、非 JSON 和空响应。

## T10：编写试卷与答案识别提示词

**文件：** `backend/homework_judge/recognition/prompts.py`
**依赖：** T2

**步骤：**
1. 定义独立、带版本号的试卷结构提示词和答案结构提示词。
2. 明确字段、题型枚举、页码来源、跨页标记和 JSON-only 输出。
3. 明确答案识别兼容精简答案和完整解析版。
4. 明确禁止解题、补写缺失内容和直接跨文件匹配。

**验证：** 快照测试确认提示词包含全部约束且不包含用户身份、文件路径或密钥。

## T11：实现模型 JSON 容错解析

**文件：** `backend/homework_judge/recognition/parser.py`
**依赖：** T10

**步骤：**
1. 从纯 JSON、代码块和带说明文本中提取对象或数组。
2. 为每个题目/答案节点独立解析，保留节点位置。
3. 拒绝字符串求值和无法确定的嵌套结构。
4. 返回合法节点、局部 ParseIssue 和原始文本摘要。

**验证：** 参数化测试覆盖所有允许响应形态、混合好坏节点和完全无可用节点。

## T12：实现识别字段归一化

**文件：** `backend/homework_judge/recognition/normalizer.py`
**依赖：** T11

**步骤：**
1. 规范化题型、分值、选项、页码、置信度和布尔标记。
2. 保留公式文本和换行，只清理无语义格式噪声。
3. 对缺字段、非法分值、未知题型和异常页码生成问题记录。
4. 把可恢复问题与阻塞问题分级。

**验证：** 单元测试覆盖中文/英文题型别名、数字字符串、空解析、坏页码和置信度越界。

## T13：实现文档分批识别与重叠合并

**文件：** `backend/homework_judge/recognition/service.py`
**依赖：** T4、T8-T12

**步骤：**
1. 按 4 页批次、1 页重叠生成有序请求。
2. 分别运行 exam 和 answer 两类识别，并逐批保存 ProcessingRun 原始响应。
3. 按规范化题号和题干指纹合并重叠重复项。
4. 保留同号不同题干冲突、跨页合并记录和全部局部解析问题。

**验证：** 模拟三批含重叠条目的响应，验证无重复丢失、顺序稳定、冲突保留和失败批次可定位。

## T14：实现题号规范化

**文件：** `backend/homework_judge/matching/numbers.py`
**依赖：** T2

**步骤：**
1. 实现 NFKC、全角数字、中文整数和题号前后缀清理。
2. 规范化常见子题格式并保留层级。
3. 对章节标题、选项字母和无法确定值返回空规范号。
4. 为每种转换返回可审计的规则名。

**验证：** 参数化测试覆盖 `1、`、`第１题`、`十二`、`1（2）`、`1-2`、章节序号和非法输入。

## T15：实现题干规范化与相似度

**文件：** `backend/homework_judge/matching/similarity.py`
**依赖：** T14

**步骤：**
1. 去除题号前缀、版式空白和不影响语义的常见标点。
2. 保留变量、数字、单位、选项标签和关键中文文本。
3. 组合 RapidFuzz token-set 与字符序列分数。
4. 实现可重复的顺序接近度计算。

**验证：** 测试同题不同排版获得高分、相似但不同数值题不被视为完全相同、无题干返回零分。

## T16：实现一对一自动匹配器

**文件：** `backend/homework_judge/matching/matcher.py`
**依赖：** T14-T15

**步骤：**
1. 建立题目侧和答案侧规范化题号索引。
2. 先执行唯一题号匹配，再为剩余条目计算题干候选。
3. 应用阈值、候选差距和一对一约束。
4. 生成匹配方式、各项分数、总分、原因、冲突、缺答案和孤立答案。

**验证：** 单元测试覆盖唯一题号、缺号高相似、重复号、竞争答案、低相似、孤立答案和重复运行确定性。

## T17：实现后台任务管理与完整流水线

**文件：** `backend/homework_judge/jobs/manager.py`、`pipeline.py`
**依赖：** T4、T13、T16

**步骤：**
1. 实现单进程任务队列、task_id 互斥和并发信号量。
2. 严格执行页面准备、题目识别、答案识别、匹配状态流转。
3. 每阶段开始、进度、成功和失败都写数据库与审计。
4. 实现重试新运行、旧运行保留和服务启动中断恢复。

**验证：** 集成测试覆盖正常流水线、重复点击、阶段失败、重试和模拟重启后的 interrupted 状态。

## T18：实现任务上传与进度 API

**文件：** `backend/homework_judge/api/tasks.py`、`router.py`
**依赖：** T4-T8、T17

**步骤：**
1. 实现任务列表、multipart 创建、详情、process 和 progress 接口。
2. 两文件校验与任务提交使用补偿清理，避免半成功。
3. process 对重复请求返回现有运行。
4. 错误统一转换为安全 API 响应。

**验证：** API 集成测试完成合法上传、四类拒绝场景、状态轮询和重复启动验证。

## T19：实现审核与完成准入 API

**文件：** `backend/homework_judge/api/review.py`
**依赖：** T4、T16

**步骤：**
1. 实现审核详情和有效值组装。
2. 实现题目覆盖、匹配修改、直接答案、确认、重开和答案忽略。
3. 在事务中验证一对一关系和修改后自动取消确认。
4. 实现服务端完成准入并返回逐项阻塞原因。

**验证：** 集成测试覆盖修改持久化、答案占用冲突、直接答案、确认回退和绕过前端完成被拒绝。

## T20：实现运行历史与安全预览 API

**文件：** `backend/homework_judge/api/runs.py`、`files.py`
**依赖：** T4-T5、T13

**步骤：**
1. 实现运行列表和单次运行详情。
2. 对原始响应和错误详情执行长度限制与敏感字段清理。
3. 实现原件和页面读取，使用数据库 ID 解析白名单相对路径。
4. 设置正确 Content-Type、下载文件名和缓存策略。

**验证：** API 测试验证运行可追溯、密钥脱敏、合法预览成功、任意路径和越权 ID 被拒绝。

## T21：组装 FastAPI 生命周期与生产静态页面

**文件：** `backend/homework_judge/main.py`、`backend/run_server.py`
**依赖：** T3、T17-T20

**步骤：**
1. 启动时加载配置、迁移数据库、恢复中断任务并创建共享客户端。
2. 关闭时停止接收任务并关闭模型客户端、数据库和任务管理器。
3. 挂载 API、异常处理、中间件和健康检查。
4. 生产模式提供前端静态资源及 SPA 路由回退。

**验证：** 启动测试验证 `/api/health`、未知 API 404、前端子路由回退和干净关闭。

## T22：建立前端应用壳与 API 客户端

**文件：** `client/index.html`、`client/src/main.tsx`、`client/src/app/`、`client/src/lib/api.ts`、`client/src/styles.css`
**依赖：** T1-T2

**步骤：**
1. 建立路由、查询客户端、全局错误边界和页面框架。
2. 实现统一响应解析、AbortSignal 和错误消息映射。
3. 定义桌面优先的排版、状态颜色和非颜色状态标签。
4. 提供加载、空状态、错误提示和确认对话框组件。

**验证：** UI 测试验证路由渲染、API 错误显示和键盘可聚焦状态。

## T23：实现任务列表与上传页面

**文件：** `client/src/features/tasks/TaskListPage.tsx`、`CreateTaskPage.tsx`
**依赖：** T18、T22

**步骤：**
1. 展示最近任务与状态汇总。
2. 实现试卷、答案两个独立文件选择区和客户端基础校验。
3. 上传时显示进度并防止重复提交。
4. 成功后导航到处理页，失败时保留文件选择和可理解错误。

**验证：** UI 测试覆盖缺文件、错误扩展名、成功提交、服务端拒绝和重复点击。

## T24：实现处理进度页面

**文件：** `client/src/features/processing/ProcessingPage.tsx`、`ProcessingSteps.tsx`
**依赖：** T18、T22

**步骤：**
1. 显示页面准备、试卷识别、答案识别、匹配四阶段。
2. 仅在处理中轮询，进入 review_pending、completed 或 failed 后停止。
3. 显示批次进度、失败阶段、错误码和重试操作。
4. 重试成功后清理旧错误并继续轮询新运行。

**验证：** 使用假时钟测试轮询启停、状态切换、失败和重试。

## T25：实现文件页面预览

**文件：** `client/src/components/FilePreview.tsx`、`PageThumbnailRail.tsx`
**依赖：** T20、T22

**步骤：**
1. 加载文档页面列表并显示缩略图和当前页。
2. 支持试卷/答案切换及来源页跳转。
3. 为图片提供缩放、适宽和清晰的页码。
4. 预览失败时显示占位错误，不影响审核表单。

**验证：** UI 测试验证页切换、来源页跳转、加载失败和图片替代文本。

## T26：实现逐题审核工作台

**文件：** `client/src/features/review/ReviewPage.tsx`、`QuestionNavigator.tsx`、`QuestionEditor.tsx`、`MatchEvidence.tsx`
**依赖：** T19、T22、T25

**步骤：**
1. 实现题目导航、状态筛选、当前题表单和页面预览三栏布局。
2. 展示自动字段、人工有效字段、答案、解析、来源页和匹配信号。
3. 保存题目与答案修改，修改已确认题时更新为待确认。
4. 实现确认、重开、上一题、下一题和快捷跳转。

**验证：** UI 测试覆盖加载题目、编辑保存、确认、修改后回退、筛选和键盘导航。

## T27：实现答案重配与异常面板

**文件：** `client/src/features/review/AnswerAssignment.tsx`、`UnmatchedPanel.tsx`
**依赖：** T19、T26

**步骤：**
1. 展示未使用答案、候选分数和来源页。
2. 支持选择、解除、直接填写答案以及标记无关条目。
3. 对答案已被占用和候选冲突显示明确阻塞说明。
4. 完成按钮展示服务端准入摘要并只在全部满足时可执行。

**验证：** UI 测试覆盖手动重配、占用冲突、孤立答案忽略、直接答案和完成失败/成功。

## T28：补齐端到端 API 测试

**文件：** `backend/tests/integration/test_workflow.py`、`test_security.py`
**依赖：** T18-T21

**步骤：**
1. 用模拟模型响应跑通上传到完成的整条 API 流程。
2. 构造精简答案、解析版、重复题号和低相似度响应。
3. 验证重试保留历史、重启恢复和完成准入。
4. 扫描响应、数据库和日志，确认测试密钥不出现。

**验证：** 运行相关 pytest，全部用例通过且没有遗留任务或临时目录。

## T29：补齐前端集成与视觉检查

**文件：** `tests/ui/workflow.test.tsx`、`scripts/visual-check.mjs`
**依赖：** T23-T27

**步骤：**
1. 使用 Mock Service Worker 或等价请求 mock 跑通上传、进度和审核流程。
2. 检查 1280×720 下页面无横向溢出，关键操作可见。
3. 检查冲突、低置信度和完成阻塞同时有文本/图标表达。
4. 输出仅供内部 QA 的截图，不提交运行时敏感数据。

**验证：** `npm run test:ui` 和视觉检查脚本通过，人工查看截图无重叠、裁切或不可操作区域。

## T30：用仓库真实样本执行受控验收

**文件：** `data/dataset/`（只读输入）、`docs/acceptance-report.md`
**依赖：** T28-T29

**步骤：**
1. 选择一组“原卷版＋解析版”样本和 `(14)` 的“试卷＋答案”样本。
2. 使用真实百炼配置分别执行两条完整流程。
3. 逐题记录识别数、匹配方式、待处理项、模型 ID、运行 ID 和 Token 用量。
4. 人工核对所有题目均已匹配或明确列为异常，不存在重复答案占用和静默丢失。

**验证：** `docs/acceptance-report.md` 包含两组实际证据，checklist.md 的端到端条目均有结论。

## T31：完善启动、配置与故障排查文档

**文件：** `README.md`、`.env.example`
**依赖：** T21、T30

**步骤：**
1. 写明 Windows 环境、Python/Node/LibreOffice 前置条件和依赖安装。
2. 写明百炼 API Key、Base URL、Workspace 域名和模型配置。
3. 写明开发、构建、生产启动、数据备份、重试和常见文件错误。
4. 写明首版范围与隐私边界。

**验证：** 在新目录按 README 从安装到启动执行一次，健康检查和本地网页均可访问。

## T32：执行最终质量门禁

**文件：** 全部实现文件
**依赖：** T1-T31

**步骤：**
1. 运行后端格式化、lint、mypy 和全部 pytest。
2. 运行前端 typecheck、测试和生产构建。
3. 启动生产服务并验证 SPA 刷新、API、文件预览和干净关闭。
4. 按 checklist.md 逐项记录实际证据，修复后重新执行失败项。

**验证：** 所有自动化命令退出码为 0，checklist.md 无未解释失败项。

## 执行顺序

```text
T1 → T2 → T3 → T4
          ├→ T5 → T6 → T7 → T8
          ├→ T9 → T10 → T11 → T12 → T13
          └→ T14 → T15 → T16

T4 + T8 + T13 + T16 → T17 → T18 → T19 → T20 → T21

T1 + T2 → T22 → T23 → T24 → T25 → T26 → T27

T21 + T27 → T28 → T29 → T30 → T31 → T32
```

## 公式显示与无门槛编辑增量任务

### 增量文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 修改 | `package.json`、`pnpm-lock.yaml` | 固定 KaTeX、MathLive 及类型依赖 |
| 新建 | `client/src/lib/math-content.ts` | 公式分隔符解析、校验、规范化和序列化 |
| 新建 | `client/src/components/MathContent.tsx` | 文字与公式的安全阅读视图 |
| 新建 | `client/src/components/FormulaEditor.tsx` | MathLive 可视化单公式编辑面板 |
| 新建 | `client/src/components/MathContentEditor.tsx` | 普通文字与公式对象的混合编辑 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 接入题干、选项、答案和解析字段 |
| 修改 | `client/src/styles.css`、必要的前端入口/类型声明 | 公式样式、本地字体和 Web Component 类型 |
| 新建 | `tests/ui/math-content-parser.test.ts` | 解析与往返稳定性测试 |
| 新建 | `tests/ui/math-content.test.tsx` | 阅读渲染与安全降级测试 |
| 新建 | `tests/ui/math-content-editor.test.tsx` | 编辑、取消、键盘和禁用状态测试 |
| 修改 | 现有审核页 UI 测试 | 保存、关联答案和固定操作栏回归测试 |

## T33：安装并配置本地公式依赖

**依赖：** 已通过的增量 Spec 与 Plan
**预估工作量：** 0.5 人日

**交付物：**

1. 固定兼容当前 React、TypeScript 和 Vite 的 KaTeX、MathLive 版本并更新锁文件。
2. 配置 KaTeX 样式和 MathLive 字体由 Vite 打入本地构建产物，不引用 CDN。
3. 增加 `math-field` 的 TypeScript JSX 类型声明和集中初始化配置。

**验证：** 断网条件下运行现有页面，开发模式和生产构建均能加载公式字体；`pnpm typecheck` 通过。

## T34：实现公式内容解析与稳定序列化

**依赖：** T33
**预估工作量：** 1 人日

**交付物：**

1. 实现四类分隔符、转义字符、行内/独立公式和异常片段的有限状态解析器。
2. 实现相邻文字合并、空片段处理、原分隔符保留和新公式规范化。
3. 实现 KaTeX 非信任配置下的公式校验和错误结果。
4. 先完成解析、危险命令、异常输入和三次往返稳定性单元测试。

**验证：** `tests/ui/math-content-parser.test.ts` 全部通过；任意失败输入均返回可显示片段而不是抛出未处理异常。

## T35：实现安全公式阅读视图

**依赖：** T34
**预估工作量：** 0.5 人日

**交付物：**

1. 实现 `MathContent`，按片段显示中文、换行、行内公式和独立公式。
2. 配置 KaTeX 的非信任、尺寸和展开限制，不把模型内容直接注入 DOM。
3. 实现异常公式原文、“公式需检查”状态和长公式滚动。

**验证：** 渲染、安全和异常降级测试通过；使用真实题目字符串可看到排版后的公式。

## T36：实现可视化单公式编辑器

**依赖：** T33、T34
**预估工作量：** 1 人日

**交付物：**

1. 封装 MathLive `math-field`，教师界面不出现 LaTeX 源码。
2. 配置数字、符号、字母和希腊字母键盘布局，以及高中物理常用结构快捷按钮。
3. 实现确认、取消、校验、撤销/重做和打开/关闭后的焦点恢复。
4. 处理初始化或字体失败状态，不影响页面其他功能。

**验证：** 可仅用鼠标或键盘录入和修改分数、根号、上下标、矢量、积分等公式；确认与取消测试通过。

## T37：实现文字与公式混合编辑器

**依赖：** T34-T36
**预估工作量：** 2 人日

**交付物：**

1. 实现阅读态、编辑态和编辑快照。
2. 普通文字可连续输入；公式显示为不可拆分、可聚焦的对象。
3. 支持在当前光标插入行内/独立公式，点击或按 Enter 修改公式，Backspace/Delete 删除公式。
4. 粘贴内容按纯文本清洗；完成编辑时序列化，取消时恢复原值。
5. 实现 `disabled`、错误提示、焦点与键盘可访问性。

**验证：** `math-content-editor` 交互测试全部通过；混合中文与多个公式连续编辑、取消和三次保存后内容稳定。

## T38：接入审核页并完成布局

**依赖：** T37
**预估工作量：** 1 人日

**交付物：**

1. 将题干、各选项、标准答案和解析接入 `MathContentEditor`。
2. 保持题目本地状态、答案关联锁定、保存修改、确认和切题逻辑不变。
3. 完成行内/独立公式、选中态、异常态、编辑面板和虚拟键盘样式。
4. 保证中间栏滚动、长公式和底部固定操作栏在 1280×720 下可用。

**验证：** 审核页回归测试通过；关联答案字段只读但公式正常显示；保存请求内容与既有 API 契约一致。

## T39：完成综合验证与修复

**依赖：** T38
**预估工作量：** 1 人日

**交付物：**

1. 运行前端类型检查、UI 测试、完整测试和生产构建，修复全部新增回归。
2. 使用已有真实物理任务核对第 1 题及第 10～15 题的题干、选项、答案和解析。
3. 在 Chrome、Edge、1280×720、全屏、离线和仅键盘场景完成浏览器验收。
4. 按增量 checklist 记录证据；若混合编辑未达标，执行 Plan 中的局部回滚方案并保留只读公式显示。

**验证：** 增量验收标准 AC17-AC25 全部有可复核证据，自动化命令退出码均为 0。

## 增量执行顺序

```text
T33 → T34 → T35
       └──→ T36 → T37 → T38 → T39
```

**总预估：** 7 人日；其中 T37 的混合光标、粘贴和公式对象交互风险最高，必须在接入审核页前单独验收。

## 作业批改 Agent 增量任务

### 增量文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml`、`requirements.lock` | 固定 Pint、SymPy、ReportLab 依赖 |
| 修改 | `backend/homework_judge/config.py` | 批改功能开关、并发、超时与复核阈值 |
| 修改 | `backend/homework_judge/db/database.py` | 评分配置、评分细则、运行、结果、复核、事件和生成物迁移 |
| 修改 | `backend/homework_judge/schemas.py` | 批改配置、评分细则、复核和坐标请求模型 |
| 新建 | `backend/homework_judge/grading/__init__.py` | 批改包入口 |
| 新建 | `backend/homework_judge/grading/contracts.py` | 统一输入、结果、证据、工具观察与状态类型 |
| 新建 | `backend/homework_judge/grading/normalization.py` | 文本、选项和十进制规范化 |
| 新建 | `backend/homework_judge/grading/choice.py` | 单选和多选确定性评分 |
| 新建 | `backend/homework_judge/grading/numeric.py` | 科学计数法与单位等价验证 |
| 新建 | `backend/homework_judge/grading/formula.py` | 受限公式解析与数学等价验证 |
| 新建 | `backend/homework_judge/grading/fill.py` | 逐空精确匹配、模型判断与工具裁决 |
| 新建 | `backend/homework_judge/grading/calculation.py` | 评分细则草案与评分点证据判断 |
| 新建 | `backend/homework_judge/grading/dependencies.py` | 评分点依赖校验和严格扣分传播 |
| 新建 | `backend/homework_judge/grading/audit.py` | 题级与整卷确定性审计 |
| 新建 | `backend/homework_judge/grading/review.py` | 复核处理、重新计算和下游失效 |
| 新建 | `backend/homework_judge/grading/router.py` | 按已确认题型调用固定批改器 |
| 新建 | `backend/homework_judge/grading/prompts.py` | 评分细则、填空和计算题结构化提示词 |
| 新建 | `backend/homework_judge/jobs/grading_pipeline.py` | 父级批改状态机、逐题并发、恢复和最终化 |
| 修改 | `backend/homework_judge/jobs/manager.py` | 评分任务键和模型调用并发限制 |
| 新建 | `backend/homework_judge/artifacts/__init__.py` | 生成物包入口 |
| 新建 | `backend/homework_judge/artifacts/annotation_layout.py` | 标记选型、避让和引导线布局 |
| 新建 | `backend/homework_judge/artifacts/annotations.py` | 页面批注与 PDF 生成 |
| 新建 | `backend/homework_judge/artifacts/error_report.py` | 错题报告结构校验与 PDF 生成 |
| 新建 | `backend/homework_judge/api/rubrics.py` | 评分配置和评分细则接口 |
| 新建 | `backend/homework_judge/api/grading.py` | 评分运行、逐题结果和复核接口 |
| 新建 | `backend/homework_judge/api/grading_artifacts.py` | 生成物预览与下载接口 |
| 修改 | `backend/homework_judge/api/dependencies.py`、`backend/homework_judge/api/router.py` | 注入新服务并注册路由 |
| 修改 | `backend/homework_judge/main.py` | 初始化批改服务和功能开关 |
| 修改 | `shared/contracts.ts` | 批改运行、评分细则、复核和生成物前端契约 |
| 修改 | `client/src/lib/api.ts` | 批改接口调用 |
| 新建 | `client/src/features/grading/GradingWorkspacePage.tsx` | 批改工作台容器 |
| 新建 | `client/src/features/grading/GradingProgress.tsx` | 阶段进度与失败重试 |
| 新建 | `client/src/features/grading/QuestionResultList.tsx` | 逐题结果与筛选 |
| 新建 | `client/src/features/grading/QuestionResultDetail.tsx` | 分项、证据和工具结果详情 |
| 新建 | `client/src/features/grading/ReviewDrawer.tsx` | 教师复核与错误位置修改 |
| 新建 | `client/src/features/grading/RubricEditor.tsx` | 计算题评分细则编辑与冻结 |
| 新建 | `client/src/features/grading/AnnotationOverlay.tsx` | 证据与批注 SVG 图层 |
| 新建 | `client/src/features/grading/AnnotationPreview.tsx` | 批注试卷预览与下载 |
| 新建 | `client/src/features/grading/ErrorReportPreview.tsx` | 错题报告预览与下载 |
| 修改 | `client/src/features/students/StudentSubmissionsPage.tsx`、`StudentPageOverlay.tsx` | 批改入口和图层复用 |
| 修改 | `client/src/main.tsx`、`client/src/styles.css` | 路由、布局、状态和响应式样式 |
| 新建/修改 | `backend/tests/unit/test_grading_*.py` | 评分、依赖、审计、生成物和恢复单元测试 |
| 新建 | `backend/tests/integration/test_grading_api.py` | 批改 API 与复核闭环集成测试 |
| 新建/修改 | `tests/ui/grading-*.test.tsx` | 批改工作台、复核和预览 UI 测试 |
| 修改 | `data/grading_benchmark/` | 教师确认标签、校准集和留出集元数据 |

### 基础设施与数据模型

## T40：固定批改后端依赖

**文件：** `pyproject.toml`、`requirements.lock`
**依赖：** 已批准的增量 Plan
**步骤：** 添加并锁定兼容 Python 3.12 的 Pint、SymPy、ReportLab，禁止宽松版本范围。
**验证：** 在项目虚拟环境导入三个包并输出版本，命令退出码为 0。

## T41：增加批改配置项

**文件：** `backend/homework_judge/config.py`、`backend/tests/unit/test_config.py`
**依赖：** T40
**步骤：** 增加功能开关、逐题并发数、模型超时、重试次数和保守复核阈值，并补充边界校验。
**验证：** 运行 `python -m pytest backend/tests/unit/test_config.py`，合法配置可加载、非法范围被拒绝。

## T42：定义批改核心契约

**文件：** `backend/homework_judge/grading/__init__.py`、`backend/homework_judge/grading/contracts.py`
**依赖：** T40
**步骤：** 定义 `QuestionGradingInput`、`QuestionGradingResult`、`EvidenceRef`、`ToolObservation`、题目与评分点状态和复核原因枚举。
**验证：** 运行 `python -m compileall backend/homework_judge/grading`，并用单元测试确认非法枚举和缺少证据引用会被拒绝。

## T43：实现十进制分数规范化

**文件：** `backend/homework_judge/grading/normalization.py`、`backend/tests/unit/test_grading_normalization.py`
**依赖：** T42
**步骤：** 实现字符串到 `Decimal`、非负边界、两位小数四舍五入和规范化序列化。
**验证：** 运行对应测试，覆盖 `1/3`、`2.675`、负数、越界值和稳定往返。

## T44：迁移评分运行与逐题结果表

**文件：** `backend/homework_judge/db/database.py`
**依赖：** T42、T43
**步骤：** 新增 `grading_runs`、`grading_question_results` 及提交、状态、题目和幂等键索引，分数字段使用文本。
**验证：** 在临时数据库执行两次迁移，表结构正确且第二次执行无变化。

## T45：迁移填空配置与评分细则表

**文件：** `backend/homework_judge/db/database.py`
**依赖：** T44
**步骤：** 新增 `question_blank_definitions`、`grading_blank_results`、`rubric_versions`、`rubric_points` 和 `rubric_dependencies`。
**验证：** 数据库测试确认外键、唯一约束、分值字段和冻结版本字段存在。

## T46：迁移评分点结果与复核表

**文件：** `backend/homework_judge/db/database.py`
**依赖：** T45
**步骤：** 新增 `grading_point_results` 和 `grading_review_items`，包含直接状态、依赖后状态、证据、原因和解决信息。
**验证：** 插入不存在的运行、题目或评分点时外键失败；同一复核原因幂等写入不重复。

## T47：迁移事件与生成物表

**文件：** `backend/homework_judge/db/database.py`
**依赖：** T46
**步骤：** 新增 `grading_events`、`grading_artifacts`，包含 `result_revision`、内容摘要、当前/过期状态和文件路径。
**验证：** 测试确认一个运行同一修订版每种生成物只能有一个当前版本。

## T48：补齐评分数据库迁移测试

**文件：** `backend/tests/unit/test_database.py`、`backend/tests/unit/test_grading_database.py`
**依赖：** T44-T47
**步骤：** 覆盖全新数据库、从现有最新版升级、重复迁移、级联删除和历史结果保留。
**验证：** 运行两个数据库测试文件，全部通过。

### 评分配置与计算题评分细则

## T49：定义配置与评分细则请求模型

**文件：** `backend/homework_judge/schemas.py`
**依赖：** T42、T45
**步骤：** 增加逐空配置、同义答案、答案类型、评分点、依赖边和冻结请求模型及字段校验。
**验证：** 构造合法与非法请求，非法分值、重复点号和空答案均被模型拒绝。

## T50：实现题目评分配置查询

**文件：** `backend/homework_judge/api/rubrics.py`
**依赖：** T45、T49
**步骤：** 实现 `GET /questions/{id}/grading-config`，返回题型、满分、逐空配置与标准答案快照信息。
**验证：** API 测试确认存在题目返回完整配置，不存在题目返回统一 404。

## T51：实现题目评分配置更新

**文件：** `backend/homework_judge/api/rubrics.py`
**依赖：** T50
**步骤：** 实现配置更新事务，校验逐空分值之和、同义答案和答案类型，并写入审计事件。
**验证：** 更新后重新读取值一致；分值和错误时事务不产生部分写入。

## T52：定义评分细则草案提示词

**文件：** `backend/homework_judge/grading/prompts.py`、`backend/tests/unit/test_grading_prompts.py`
**依赖：** T42、T49
**步骤：** 建立版本化结构化提示词，输入题目、标准答案、解析和满分，输出评分点、分值、顺序及依赖。
**验证：** 测试确认提示词不缺输入字段，输出 schema 不允许模型写最终学生分数。

## T53：实现评分细则依赖校验器

**文件：** `backend/homework_judge/grading/dependencies.py`、`backend/tests/unit/test_grading_dependencies.py`
**依赖：** T42
**步骤：** 实现点号唯一、引用存在、分值和、拓扑排序与环检测。
**验证：** 运行测试，合法 DAG 通过，自环、间接环、悬空引用和分值和错误均失败。

## T54：实现评分细则草案生成

**文件：** `backend/homework_judge/grading/calculation.py`、`backend/homework_judge/api/rubrics.py`
**依赖：** T52、T53
**步骤：** 调用现有模型客户端解析结构化草案，校验后保存为未冻结版本，非法输出转为可见错误。
**验证：** 使用模型桩返回合法与非法 JSON，只有合法草案被保存。

## T55：实现评分细则查看与编辑

**文件：** `backend/homework_judge/api/rubrics.py`
**依赖：** T54
**步骤：** 实现版本列表与未冻结版本事务更新；冻结版本更新返回冲突错误。
**验证：** API 测试确认草案可编辑、旧版本可查询、冻结版本不可原地修改。

## T56：实现评分细则冻结

**文件：** `backend/homework_judge/api/rubrics.py`
**依赖：** T53、T55
**步骤：** 冻结前重新执行完整校验，保存确认人、时间和内容摘要，并阻止无效版本冻结。
**验证：** 合法版本冻结后不可变；有环或分值不等于满分的版本返回 409。

## T57：注册评分配置与细则路由

**文件：** `backend/homework_judge/api/router.py`、`backend/homework_judge/api/dependencies.py`
**依赖：** T50-T56
**步骤：** 注册 `rubrics` 路由和模型客户端依赖，保持功能开关关闭时现有路由不受影响。
**验证：** 启动测试应用后新路由可访问，原有任务和审核接口回归测试通过。

## T58：完成评分细则 API 集成测试

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T57
**步骤：** 覆盖配置读取更新、草案生成、编辑、冻结、非法 DAG 和版本历史。
**验证：** 运行 `python -m pytest backend/tests/integration/test_grading_api.py -k rubric` 全部通过。

### 确定性评分核心

## T59：实现选项答案规范化

**文件：** `backend/homework_judge/grading/normalization.py`、`backend/tests/unit/test_grading_choice.py`
**依赖：** T43
**步骤：** 将大小写、分隔符、重复选项和空白识别结果转换为有序唯一选项集合，并保留解析问题。
**验证：** `"a,c,A"` 得到 `A,C`，无法解析字符产生问题而非被静默丢弃。

## T60：实现单选题评分器

**文件：** `backend/homework_judge/grading/choice.py`、`backend/tests/unit/test_grading_choice.py`
**依赖：** T42、T59
**步骤：** 实现完全匹配满分以及错选、多选、空白零分；低置信度结果附加复核原因。
**验证：** 单选四类用例和低置信度用例全部通过。

## T61：实现多选题评分器

**文件：** `backend/homework_judge/grading/choice.py`、`backend/tests/unit/test_grading_choice.py`
**依赖：** T43、T59
**步骤：** 实现全对满分、错选零分、正确真子集比例分和两位小数，并保存比例与未舍入值。
**验证：** 正确答案 `ACD`、满分 6 时，`ACD/AC/A/AB/空白` 分别得到 `6.00/4.00/2.00/0.00/0.00`。

## T62：实现填空文本与同义答案匹配

**文件：** `backend/homework_judge/grading/normalization.py`、`backend/tests/unit/test_grading_fill.py`
**依赖：** T43
**步骤：** 实现 Unicode、空格、允许标点和大小写规范化，并与标准答案及教师同义答案完全匹配。
**验证：** 配置内等价写法命中，未配置的语义相近文本不被规则直接放行。

## T63：实现数值与单位验证器

**文件：** `backend/homework_judge/grading/numeric.py`、`backend/tests/unit/test_grading_numeric.py`
**依赖：** T40、T43
**步骤：** 解析十进制与科学计数法，使用 Pint 校验量纲和单位换算，返回等价、不等价或无法判断及证据。
**验证：** `100 cm=1 m`、等价科学计数法通过，量纲冲突失败，未知单位返回无法判断。

## T64：实现受限公式解析器

**文件：** `backend/homework_judge/grading/formula.py`、`backend/tests/unit/test_grading_formula.py`
**依赖：** T40
**步骤：** 建立允许的符号、函数、复杂度和变量假设，拒绝任意代码、超长或不支持表达式。
**验证：** 常见代数式可解析，危险、超长和未知函数输入被安全拒绝。

## T65：实现公式等价验证器

**文件：** `backend/homework_judge/grading/formula.py`、`backend/tests/unit/test_grading_formula.py`
**依赖：** T64
**步骤：** 使用 SymPy 在超时边界内比较规范化表达式，返回等价、不等价或无法判断，并保留变量条件。
**验证：** 展开式、因式分解式和简单分式等价用例通过；定义域不明确或超时返回无法判断。

## T66：实现严格依赖传播

**文件：** `backend/homework_judge/grading/dependencies.py`、`backend/tests/unit/test_grading_dependencies.py`
**依赖：** T53
**步骤：** 从失败点遍历全部后继点，设置 `blocked_by_dependency`、零分和阻断祖先；独立点保持自身结果。
**验证：** `P1→P2→P3` 与独立 `P4` 用例中，P1 失败只清零 P1-P3。

## T67：实现题级评分审计

**文件：** `backend/homework_judge/grading/audit.py`、`backend/tests/unit/test_grading_audit.py`
**依赖：** T42、T43、T66
**步骤：** 检查题目分数范围、分项和、证据存在、依赖状态和模型工具冲突，返回固定复核原因。
**验证：** 每类矛盾均产生对应原因，合法结果无误报。

## T68：实现整卷评分审计

**文件：** `backend/homework_judge/grading/audit.py`、`backend/tests/unit/test_grading_audit.py`
**依赖：** T67
**步骤：** 检查所有题均已完成、总分等于题分之和、无开放复核项和引用版本完整。
**验证：** 缺题、重复题、开放复核项和总分不一致均阻止最终化。

### 大模型判断、裁决与题型路由

## T69：定义非完全一致填空提示词

**文件：** `backend/homework_judge/grading/prompts.py`、`backend/tests/unit/test_grading_prompts.py`
**依赖：** T52
**步骤：** 定义版本化输入和 `correct/incorrect/unable` 结构化输出，要求简短依据和证据引用。
**验证：** schema 拒绝纯分数、越界置信值、缺少结论或不存在证据引用。

## T70：实现逐空模型判断调用

**文件：** `backend/homework_judge/grading/fill.py`
**依赖：** T62、T69
**步骤：** 对所有规则未完全匹配的空调用模型，记录模型、提示词版本、耗时、重试与结构化结果。
**验证：** 模型桩测试确认每个非完全匹配空恰好调用一次，完全匹配空不调用。

## T71：实现填空模型与工具裁决

**文件：** `backend/homework_judge/grading/fill.py`、`backend/tests/unit/test_grading_fill.py`
**依赖：** T63、T65、T70
**步骤：** 按 `answer_kind` 合并模型与验证器结果，一致时给满分或零分，冲突、无法判断或低置信度时创建复核原因。
**验证：** 文本、数值、公式一致与冲突矩阵全部得到预期状态，单空不出现部分分。

## T72：定义计算题评分点判断提示词

**文件：** `backend/homework_judge/grading/prompts.py`、`backend/tests/unit/test_grading_prompts.py`
**依赖：** T52、T53
**步骤：** 限制模型逐点评估冻结评分点，只返回直接状态、证据、原因和新解法标记，不返回总分。
**验证：** schema 拒绝模型新增评分点、修改分值、引用未知点或直接写总分。

## T73：实现计算题评分点证据判断

**文件：** `backend/homework_judge/grading/calculation.py`、`backend/tests/unit/test_grading_calculation.py`
**依赖：** T54、T72
**步骤：** 聚合有序多区域作答，调用模型并验证每个满足点具有可定位证据，标记无法判断和未覆盖新解法。
**验证：** 模型桩覆盖满足、失败、无证据、无法判断和新解法五种结果。

## T74：实现计算题确定性计分

**文件：** `backend/homework_judge/grading/calculation.py`、`backend/tests/unit/test_grading_calculation.py`
**依赖：** T66、T73
**步骤：** 按冻结分值赋直接分，运行严格依赖传播并汇总题目分数，不允许模型分数覆盖。
**验证：** 多层依赖、独立点和满分上限测试全部通过。

## T75：实现固定题型路由器

**文件：** `backend/homework_judge/grading/router.py`、`backend/tests/unit/test_grading_router.py`
**依赖：** T60、T61、T71、T74
**步骤：** 仅依据教师确认题型调用四种批改器，阻止模型改变题型，并拒绝不支持类型。
**验证：** 四种类型各只调用对应工具；未知或未确认题型返回阻断错误。

## T76：补齐模型输出与冲突回归测试

**文件：** `backend/tests/unit/test_grading_fill.py`、`test_grading_calculation.py`、`test_grading_router.py`
**依赖：** T69-T75
**步骤：** 增加缺字段、非法枚举、越界值、工具冲突、低置信度和重试耗尽用例。
**验证：** 三个测试文件全部通过，任何无效输出都不能产生最终分。

### 批改流水线与恢复

## T77：实现评分运行预检查

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`
**依赖：** T48、T56、T75
**步骤：** 校验学生提交就绪、四类题型已确认、标准答案存在、填空配置完整和计算题评分细则已冻结。
**验证：** 每种缺失条件返回稳定错误码且不创建部分题目结果。

## T78：实现运行输入快照

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/unit/test_grading_pipeline.py`
**依赖：** T77
**步骤：** 创建运行时保存题型、标准答案、评分配置、评分细则、识别版本和输入摘要。
**验证：** 上游数据之后改变时，旧运行快照保持不变且输入摘要可复现。

## T79：实现逐题受控并发

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/homework_judge/jobs/manager.py`
**依赖：** T41、T78
**步骤：** 以题目为独立工作单元，使用配置化信号量限制模型调用，单题异常转换为失败或复核结果。
**验证：** 并发测试确认峰值不超过配置，故意失败一题时其他题仍完成。

## T80：实现逐题幂等保存

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/unit/test_grading_pipeline.py`
**依赖：** T44、T79
**步骤：** 使用运行、题目和输入摘要组成幂等键，事务保存题目、填空和评分点结果及工具观察。
**验证：** 同一题重复执行不产生重复记录，输入变化时产生新的有效结果版本。

## T81：实现题级审计与复核项创建

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`
**依赖：** T67、T80
**步骤：** 保存批改器输出后运行题级审计，对固定原因幂等创建复核项，无风险题进入 `final`。
**验证：** 冲突结果创建一个开放复核项，重复恢复不创建第二个。

## T82：实现整卷审计与自动最终化

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`
**依赖：** T68、T81
**步骤：** 汇总最终题分并运行整卷审计；有开放项进入 `needs_review`，无风险时生成结果修订和摘要。
**验证：** 无风险运行自动进入生成阶段，有复核项时停在 `needs_review`。

## T83：实现阶段检查点与安全恢复

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/unit/test_grading_pipeline.py`
**依赖：** T80-T82
**步骤：** 保存最后成功阶段、尝试次数、错误分类和可重试标志；恢复时跳过输入未变的完成题目。
**验证：** 在评分中途注入异常后再次运行，只执行未完成题目并得到一致总分。

## T84：扩展后台任务管理器

**文件：** `backend/homework_judge/jobs/manager.py`、`backend/tests/unit/test_job_manager.py`
**依赖：** T79
**步骤：** 区分识别任务键与评分运行键，支持查询、取消和关闭评分任务而不影响其他任务。
**验证：** 同一运行不会重复启动，不同提交可并行，取消评分任务不取消识别任务。

## T85：完成批改流水线单元测试

**文件：** `backend/tests/unit/test_grading_pipeline.py`
**依赖：** T77-T84
**步骤：** 覆盖完整状态序列、预检查失败、单题失败、复核暂停、自动最终化、中断恢复和幂等重试。
**验证：** 运行该测试文件全部通过，数据库最终状态与事件序列一致。

### 评分运行与教师复核 API

## T86：实现评分运行创建接口

**文件：** `backend/homework_judge/api/grading.py`、`backend/homework_judge/api/dependencies.py`
**依赖：** T77、T84
**步骤：** 实现创建运行接口、重复启动保护、功能开关检查和后台任务启动。
**验证：** 合法提交返回新运行 ID；未就绪、关闭功能或重复启动返回稳定错误。

## T87：实现评分运行列表与详情接口

**文件：** `backend/homework_judge/api/grading.py`
**依赖：** T86
**步骤：** 返回历史运行、阶段进度、题目统计、总分、开放复核数、当前修订和错误信息。
**验证：** 新旧运行按时间排序，旧运行可查看且不会被当前运行覆盖。

## T88：实现逐题评分结果接口

**文件：** `backend/homework_judge/api/grading.py`
**依赖：** T80、T87
**步骤：** 实现逐题列表和详情，返回分项、证据、工具结论及简短原因，不暴露模型内部推理。
**验证：** 接口数据与数据库快照一致，其他运行的题目 ID 不能越权读取。

## T89：实现复核项列表与详情接口

**文件：** `backend/homework_judge/api/grading.py`
**依赖：** T81、T88
**步骤：** 返回开放/已解决复核项、题目上下文、学生证据图、标准答案、细则和触发原因。
**验证：** 只筛选开放项时数量正确，证据 URL 均指向本运行允许的文件。

## T90：实现复核解决服务

**文件：** `backend/homework_judge/grading/review.py`、`backend/homework_judge/api/grading.py`
**依赖：** T66-T68、T89
**步骤：** 支持确认、修正识别、修改填空判断和修改评分点直接状态，记录教师与原因后重新计算和审计。
**验证：** 修改基础结果后题分、依赖点、总分和复核状态同步更新，不能直接写入矛盾总分。

## T91：实现错误位置修改与下游失效

**文件：** `backend/homework_judge/grading/review.py`、`backend/homework_judge/api/grading.py`
**依赖：** T47、T90
**步骤：** 校验页面与坐标范围，保存最终证据，增加 `result_revision` 并将相关批注和报告标记过期。
**验证：** 越界坐标被拒绝；合法修改只使本运行的相关生成物失效。

## T92：实现评分运行重试与生成物重建接口

**文件：** `backend/homework_judge/api/grading.py`
**依赖：** T83、T91
**步骤：** 实现安全重试与只重建生成物两个操作，检查运行状态和输入摘要。
**验证：** 评分失败重试恢复流水线，单纯生成失败不会再次调用题型批改器。

## T93：注册评分运行 API

**文件：** `backend/homework_judge/api/router.py`、`backend/homework_judge/main.py`
**依赖：** T86-T92
**步骤：** 注册 `grading` 路由，初始化流水线与依赖，并保证应用关闭时正确取消后台评分任务。
**验证：** 应用生命周期测试通过，关闭服务后无遗留运行中任务。

## T94：完成评分与复核 API 集成测试

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T93
**步骤：** 覆盖创建、查询、复核、重新审计、重试、历史运行和错误位置更新。
**验证：** 运行 `python -m pytest backend/tests/integration/test_grading_api.py -k "run or review"` 全部通过。

### 批注试卷与错题报告

## T95：定义批注标记与生成物契约

**文件：** `backend/homework_judge/artifacts/__init__.py`、`backend/homework_judge/artifacts/annotation_layout.py`
**依赖：** T42、T47
**步骤：** 定义勾、红圈、部分分和待复核标记，包含锚点、几何、标签、证据和修订号。
**验证：** 契约拒绝未知标记、越界几何和缺少证据的红圈。

## T96：实现题目结果到标记的映射

**文件：** `backend/homework_judge/artifacts/annotation_layout.py`、`backend/tests/unit/test_annotation_layout.py`
**依赖：** T95
**步骤：** 将满分、零分、部分分和待复核状态映射为规定标记；计算题只选择首个实际错误。
**验证：** 四类状态和依赖连带失分用例生成正确标记数量与类型。

## T97：实现批注避让与引导线布局

**文件：** `backend/homework_judge/artifacts/annotation_layout.py`、`backend/tests/unit/test_annotation_layout.py`
**依赖：** T96
**步骤：** 按候选位置检测页面边界、受保护区域和已有标记碰撞，必要时移动到页边并生成引导线。
**验证：** 几何快照全部在页内、不覆盖保护框，拥挤用例产生引导线。

## T98：实现批注页面渲染

**文件：** `backend/homework_judge/artifacts/annotations.py`、`backend/tests/unit/test_annotations.py`
**依赖：** T40、T97
**步骤：** 使用 Pillow/OpenCV 在学生原页副本上绘制形状、文字和引导线，并加载打包中文字体。
**验证：** 输出图像尺寸与原页一致，原文件摘要不变，标记像素落在布局框内。

## T99：实现多页批注 PDF

**文件：** `backend/homework_judge/artifacts/annotations.py`、`backend/tests/unit/test_annotations.py`
**依赖：** T98
**步骤：** 按原页序组装页面并生成独立 PDF，同时保存 `marks.json` 和内容摘要。
**验证：** 渲染 PDF 后页数、页序和尺寸正确，中文分数标记可提取或可视检查。

## T100：实现结构化错题反馈生成

**文件：** `backend/homework_judge/artifacts/error_report.py`、`backend/tests/unit/test_error_report.py`
**依赖：** T69、T82
**步骤：** 只使用最终结果与证据调用反馈模型，生成汇总、错题原因、知识点和简短建议。
**验证：** 满分题不进入错题列表，部分分题包含首个失分点与依赖说明。

## T101：实现错题报告一致性校验

**文件：** `backend/homework_judge/artifacts/error_report.py`、`backend/tests/unit/test_error_report.py`
**依赖：** T100
**步骤：** 校验得分、证据引用、错误原因来源、内容长度，并拦截完整标准答案或完整解题过程。
**验证：** 篡改分数、未知证据、过长解答和证据不支持原因均被拒绝。

## T102：实现错题报告 PDF

**文件：** `backend/homework_judge/artifacts/error_report.py`、`backend/tests/unit/test_error_report.py`
**依赖：** T40、T101
**步骤：** 使用固定中文字体渲染汇总、真实裁剪图和逐题反馈，保存 JSON、PDF 与内容摘要。
**验证：** PDF 页数大于零，中文、分数和裁剪图可见，报告修订号与最终结果一致。

## T103：实现生成物版本服务

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/homework_judge/grading/review.py`
**依赖：** T47、T99、T102
**步骤：** 使用生成物幂等键创建、失败重试、设置当前版本和过期旧版本，两类文件使用同一结果修订。
**验证：** 同一修订重复生成不产生重复当前文件，教师修改后旧文件立即过期。

## T104：实现生成物预览与下载接口

**文件：** `backend/homework_judge/api/grading_artifacts.py`、`backend/homework_judge/api/router.py`
**依赖：** T103
**步骤：** 实现版本列表、当前预览和安全下载，复用现有路径约束并阻止下载过期版本作为当前结果。
**验证：** 合法当前文件可预览下载，路径穿越、其他运行文件和过期默认下载均被拒绝。

## T105：完成生成物自动化测试

**文件：** `backend/tests/unit/test_annotation_layout.py`、`test_annotations.py`、`test_error_report.py`、`backend/tests/integration/test_grading_api.py`
**依赖：** T95-T104
**步骤：** 补齐坐标不可靠转复核、碰撞、中文 PDF、生成失败重试、修改后重生和下载安全用例。
**验证：** 四个测试文件全部通过，原始学生文件摘要始终不变。

### 前端批改工作台

## T106：增加前端批改契约与 API 客户端

**文件：** `shared/contracts.ts`、`client/src/lib/api.ts`
**依赖：** T58、T94、T104
**步骤：** 增加运行、题目结果、评分点、复核项、标记与生成物类型及对应请求函数。
**验证：** `pnpm typecheck` 通过，接口响应不使用 `any`。

## T107：建立批改工作台路由与页面壳

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`、`client/src/main.tsx`
**依赖：** T106
**步骤：** 增加学生提交批改路由、运行查询、加载、空状态和错误边界。
**验证：** UI 测试能从路由参数加载正确运行，并显示加载与 404 状态。

## T108：接入学生列表批改入口

**文件：** `client/src/features/students/StudentSubmissionsPage.tsx`
**依赖：** T107
**步骤：** 增加开始批改、查看进度、待复核数量和进入结果按钮，未满足预检查时显示明确原因。
**验证：** 交互测试确认按钮状态和导航目标随提交状态正确变化。

## T109：实现批改阶段进度组件

**文件：** `client/src/features/grading/GradingProgress.tsx`
**依赖：** T107
**步骤：** 显示阶段、题目进度、重试状态、错误信息和可安全重试操作。
**验证：** 使用各状态桩渲染时标签正确，重试按钮只在允许状态出现。

## T110：实现逐题结果列表与筛选

**文件：** `client/src/features/grading/QuestionResultList.tsx`
**依赖：** T106、T107
**步骤：** 展示题号、题型、得分和状态，并实现全部、待复核、错误、部分分筛选。
**验证：** UI 测试确认筛选数量、选中题和键盘导航正确。

## T111：实现逐题评分详情

**文件：** `client/src/features/grading/QuestionResultDetail.tsx`
**依赖：** T110
**步骤：** 显示题目、标准答案、学生识别、分项、证据、工具结论和简短原因，不显示模型内部推理。
**验证：** 单选、填空和计算题数据桩均能完整渲染，缺失可选字段不会崩溃。

## T112：实现计算题评分细则编辑器

**文件：** `client/src/features/grading/RubricEditor.tsx`
**依赖：** T58、T106
**步骤：** 实现草案生成、评分点增删改排、依赖选择、分值和提示、冻结及只读历史。
**验证：** UI 测试确认有环、分值和错误时不能冻结，冻结后字段只读。

## T113：实现教师复核抽屉

**文件：** `client/src/features/grading/ReviewDrawer.tsx`
**依赖：** T111
**步骤：** 展示复核原因和上下文，支持确认、修正识别、修改填空/评分点状态及填写修改原因。
**验证：** 每种复核动作发送正确结构，保存后刷新题目、总分和开放项数量。

## T114：扩展学生页面证据与批注图层

**文件：** `client/src/features/students/StudentPageOverlay.tsx`、`client/src/features/grading/AnnotationOverlay.tsx`
**依赖：** T95、T106
**步骤：** 增加作答区域、评分证据和批注图层开关，使用后端几何绘制勾、红圈、三角、文字和引导线。
**验证：** SVG 测试确认各标记形状和坐标正确，隐藏图层后原图不受影响。

## T115：实现错误位置拖选与调整

**文件：** `client/src/features/grading/AnnotationOverlay.tsx`、`client/src/features/grading/ReviewDrawer.tsx`
**依赖：** T91、T114
**步骤：** 支持教师在当前学生页面拖选或调整错误框，限制在页面内并提交原始像素坐标。
**验证：** 缩放预览后提交坐标仍映射到原图，越界拖动被裁剪或拒绝。

## T116：实现批注试卷预览

**文件：** `client/src/features/grading/AnnotationPreview.tsx`
**依赖：** T104、T106
**步骤：** 展示页面导航、当前/过期/生成失败状态，以及预览、重试和下载入口。
**验证：** UI 测试确认过期文件不能显示为当前，下载使用正确生成物 ID。

## T117：实现错题报告预览

**文件：** `client/src/features/grading/ErrorReportPreview.tsx`
**依赖：** T104、T106
**步骤：** 展示分数汇总、题型统计、错题裁剪、原因、知识点与建议，并提供下载。
**验证：** 满分题不出现在逐题列表，部分分计算题显示依赖扣分说明。

## T118：组装批改工作台布局

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`、`client/src/styles.css`
**依赖：** T109-T117
**步骤：** 组装学生与题目导航、试卷预览、复核抽屉、结果标签页和固定操作区，处理 1280×720 滚动。
**验证：** 组件集成测试通过，主要操作在 1280×720 下无需横向页面滚动。

## T119：完成前端批改回归测试

**文件：** `tests/ui/grading-workspace.test.tsx`、`tests/ui/grading-review.test.tsx`、`tests/ui/grading-artifacts.test.tsx`
**依赖：** T118
**步骤：** 覆盖自动完成、待复核、修改结果、坐标调整、过期重生、预览和下载。
**验证：** `pnpm exec vitest run tests/ui/grading-*.test.tsx` 全部通过。

### 端到端、评测与质量门禁

## T120：建立教师确认标签元数据格式

**文件：** `data/grading_benchmark/README.md`、`data/grading_benchmark/labels.schema.json`
**依赖：** T42
**步骤：** 定义题型、逐空、评分点、证据框、最终分、复核原因、确认人和确认时间字段，标记未确认样本。
**验证：** 现有合成标签可被校验但保持 `unreviewed`，不能误标为正式验收集。

## T121：增加客观题端到端场景

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T94、T105
**步骤：** 从已就绪学生提交启动评分，覆盖单选全对和多选满分、少选、错选，检查最终分与批注。
**验证：** 场景自动完成，分数和标记与 AC33-AC35 一致。

## T122：增加填空题端到端场景

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T94、T105
**步骤：** 覆盖多空不同分值、同义答案、非完全一致模型判断、数值/公式一致与冲突复核。
**验证：** 逐空状态、题目和、复核项和最终报告与 AC36-AC38 一致。

## T123：增加计算题依赖端到端场景

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T94、T105
**步骤：** 使用冻结细则构造多层依赖与独立点，检查评分点证据、严格传播、题分和首错标记。
**验证：** 结果与 AC39-AC41 一致，模型返回的任何总分字段均不生效。

## T124：增加教师修改与重新生成场景

**文件：** `backend/tests/integration/test_grading_api.py`、`tests/ui/grading-review.test.tsx`
**依赖：** T119、T123
**步骤：** 解决复核、修改识别和错误位置，检查重新审计、修订增加、旧生成物过期和新文件内容。
**验证：** API 与 UI 场景都满足 AC43-AC50。

## T125：增加中断恢复与幂等场景

**文件：** `backend/tests/unit/test_grading_pipeline.py`、`backend/tests/integration/test_grading_api.py`
**依赖：** T83、T103
**步骤：** 在逐题评分、批注和报告阶段分别注入中断并恢复，统计模型调用和数据库记录。
**验证：** 已完成题目不重复调用或计分，最终无重复结果和生成物。

## T126：增加可观测性与隐私检查

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/integration/test_grading_api.py`
**依赖：** T103
**步骤：** 记录阶段耗时、模型用量、重试、失败和复核原因，并检查日志不含密钥、无关身份或完整文件。
**验证：** 测试日志能按运行和题目关联，敏感测试值不出现在捕获日志中。

## T127：执行浏览器与 PDF 人工验收

**文件：** `checklist.md` 对应执行记录
**依赖：** T119、T124
**步骤：** 在最新版 Chrome、Edge 和 1280×720 下走上传到下载流程，检查批注不遮挡、非彩色可辨和中文 PDF。
**验证：** 保存页面截图、生成物样本和逐项通过/失败记录。

## T128：执行教师标签校准与留出评测

**文件：** `data/grading_benchmark/`、评测输出目录
**依赖：** T120-T126、教师完成标签确认
**步骤：** 划分校准集与留出集，冻结阈值后计算各题型、复核召回、自动错误率和错误位置指标。
**验证：** 输出包含数据版本、教师确认状态、模型/提示词/工具版本和全部分项指标的报告。

## T129：执行最终质量门禁

**文件：** 全部新增和修改文件
**依赖：** T121-T128
**步骤：** 依次运行 Python 测试、UI 测试、类型检查、Ruff、mypy、生产构建和编译检查，修复全部新增回归。
**验证：** `pnpm test`、`pnpm typecheck`、`pnpm lint`、`pnpm build` 均以退出码 0 完成。

## T130：更新使用与故障排查文档

**文件：** `README.md` 及现有运行说明
**依赖：** T129
**步骤：** 记录依赖安装、功能开关、评分细则准备、批改流程、复核、重试、生成物路径和阈值校准要求。
**验证：** 按文档从全新配置启动应用并完成一个模型桩批改流程，不依赖未记录步骤。

### 增量执行顺序

```text
基础与迁移：T40 → T41-T43 → T44 → T45 → T46 → T47 → T48

评分配置：T48 → T49 → T50 → T51
评分细则：T49 → T52 → T53 → T54 → T55 → T56 → T57 → T58

确定性评分：T43 → T59 → T60-T61
              T43 → T62 → T63
              T40 → T64 → T65
              T53 → T66 → T67 → T68

模型与路由：T62-T65 + T69 → T70 → T71
              T52-T53 → T72 → T73 → T74
              T60-T61 + T71 + T74 → T75 → T76

流水线：T48 + T56 + T75 → T77 → T78 → T79 → T80 → T81 → T82 → T83
任务管理：T41 + T79 → T84 → T85

API 与复核：T77 + T84 → T86 → T87 → T88 → T89 → T90 → T91 → T92 → T93 → T94

生成物：T42 + T47 → T95 → T96 → T97 → T98 → T99
          T69 + T82 → T100 → T101 → T102
          T99 + T102 → T103 → T104 → T105

前端：T58 + T94 + T104 → T106 → T107 → T108-T112
      T111 → T113
      T95 + T106 → T114 → T115
      T104 + T106 → T116-T117
      T109-T117 → T118 → T119

验收：T94 + T105 → T120-T123 → T124-T126 → T127-T128 → T129 → T130
```

建议按四个里程碑交付：

1. **M1 客观题可用：** T40-T68、T75、T77-T88 中与客观题相关部分完成。
2. **M2 四类评分与复核可用：** T69-T94 完成。
3. **M3 批注、报告和工作台可用：** T95-T119 完成。
4. **M4 正式验收：** T120-T130 完成，并取得教师确认的评测标签。

## 增量任务：非重叠识别、边界合并与重复题生命周期

### 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `backend/homework_judge/recognition/boundary.py` | 边界草稿、上下文、决策校验与安全应用 |
| 修改 | `backend/homework_judge/recognition/service.py` | 非重叠分批和边界协调 |
| 修改 | `backend/homework_judge/recognition/prompts.py` | 主识别与边界提示词 |
| 修改 | `backend/homework_judge/recognition/parser.py` | 边界响应解析 |
| 修改 | `backend/homework_judge/recognition/consolidator.py` | 最终去重与冲突标记 |
| 修改 | `backend/homework_judge/config.py`、`.env.example` | 边界置信度配置 |
| 修改 | `backend/homework_judge/jobs/pipeline.py` | 运行记录与有效题匹配输入 |
| 修改 | `backend/homework_judge/db/database.py` | v7 数据迁移 |
| 修改 | `backend/homework_judge/matching/matcher.py` | 单题安全匹配建议 |
| 新建 | `backend/homework_judge/review/__init__.py` | 审核业务包入口 |
| 新建 | `backend/homework_judge/review/invalidation.py` | 活跃运行保护和下游失效 |
| 新建 | `backend/homework_judge/review/lifecycle.py` | 重复题标记与恢复事务 |
| 修改 | `backend/homework_judge/api/review.py` | 审核详情、重复题接口和操作保护 |
| 修改 | `backend/homework_judge/api/tasks.py` | 有效题统计 |
| 修改 | `backend/homework_judge/api/grading.py` | 评分过期状态 |
| 修改 | `shared/contracts.ts`、`shared/schemas.ts` | 前后端状态契约 |
| 新建 | `client/src/features/review/ConfirmDuplicateQuestionDialog.tsx` | 标记重复确认框 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 重复题筛选、标记、恢复和只读模式 |
| 修改 | `client/src/features/grading/GradingWorkspacePage.tsx` | 评分过期提示 |
| 修改 | `client/src/styles.css` | 重复题和对话框样式 |
| 新建 | `backend/tests/unit/test_boundary_reconciliation.py` | 边界合并单元测试 |
| 新建 | `backend/tests/unit/test_question_lifecycle.py` | 重复题生命周期单元测试 |
| 修改 | `backend/tests/unit/test_recognition_batches.py` | 非重叠批次测试 |
| 修改 | `backend/tests/unit/test_consolidator.py` | 最终去重测试 |
| 修改 | `backend/tests/unit/test_matcher.py` | 单题匹配测试 |
| 修改 | `backend/tests/unit/test_database.py` | v7 迁移测试 |
| 修改 | `backend/tests/integration/test_api_workflow.py` | API 与端到端后端流程 |
| 新建 | `tests/ui/review-duplicate-question.test.tsx` | 重复题 UI 测试 |
| 修改 | `tests/ui/grading-workspace.test.tsx` | 评分过期 UI 测试 |

## T131：建立 v7 迁移测试

**文件：** `backend/tests/unit/test_database.py`
**依赖：** 无

**步骤：**
1. 构造 v6 历史数据库并插入题目、评分运行和相关记录。
2. 增加历史题目默认有效、评分默认未过期及重复迁移幂等断言。
3. 验证新索引存在且历史数据未丢失。

**验证：** 运行 `python -m pytest backend/tests/unit/test_database.py -q -p no:cacheprovider`，新增用例在实现迁移后通过。

## T132：实现 v7 数据迁移

**文件：** `backend/homework_judge/db/database.py`
**依赖：** T131

**步骤：**
1. 增加 `questions.is_duplicate` 和 `grading_runs.is_stale` 字段。
2. 增加有效题查询索引并将最新版本提升至 7。
3. 使用列存在性检查保证部分升级和重复迁移安全。

**验证：** 运行 T131 的迁移测试，期望全部通过。

## T133：增加边界合并配置与提示词

**文件：** `backend/homework_judge/config.py`、`.env.example`、`backend/homework_judge/recognition/prompts.py`、`backend/tests/unit/test_config.py`
**依赖：** 无

**步骤：**
1. 增加默认值为 `0.85` 的服务端边界合并最低置信度配置及范围校验。
2. 移除主识别提示中的重叠批次描述。
3. 增加试卷和答案角色专用边界提示词、用户提示和版本号。
4. 用快照式断言检查禁止解题、草稿引用和三态输出约束。

**验证：** 运行 `python -m pytest backend/tests/unit/test_config.py backend/tests/unit/test_recognition_batches.py -q -p no:cacheprovider`。

## T134：改为非重叠主批次

**文件：** `backend/homework_judge/recognition/service.py`、`backend/tests/unit/test_recognition_batches.py`
**依赖：** T133

**步骤：**
1. 把页面分批改为连续非重叠切片。
2. 覆盖页数少于、等于和大于批次大小的情况。
3. 断言全部主批次页码无重复且保持原顺序。

**验证：** 运行 `python -m pytest backend/tests/unit/test_recognition_batches.py -q -p no:cacheprovider`。

## T135：定义并解析边界数据结构

**文件：** `backend/homework_judge/recognition/boundary.py`、`backend/homework_judge/recognition/parser.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T133

**步骤：**
1. 定义 `RecognitionDraft`、`BoundaryContext` 和 `BoundaryDecision`。
2. 解析合法 `merge`、`separate`、`uncertain` 响应。
3. 对缺失数组、非法 relation、错误字段类型和越界置信度返回结构化解析问题。

**验证：** 运行 `python -m pytest backend/tests/unit/test_boundary_reconciliation.py -q -p no:cacheprovider` 中解析用例。

## T136：实现边界决策确定性校验

**文件：** `backend/homework_judge/recognition/boundary.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T135

**步骤：**
1. 校验草稿存在、分属两侧、未重复消费且来源页合法。
2. 校验题号兼容、角色字段完整和最低置信度。
3. 将所有非法合并统一降级为 `uncertain` 并保留原因。

**验证：** 运行边界单元测试，覆盖伪造草稿 ID、错误页码、题号冲突、低置信度和重复引用。

## T137：应用试卷边界合并

**文件：** `backend/homework_judge/recognition/boundary.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T136

**步骤：**
1. 使用现有题目规范化器处理 `mergedItem`。
2. 合并题干、选项、题型、分值、来源页、答题区域和整题区域。
3. 用合法合并替换被引用草稿，保留未引用草稿顺序。

**验证：** 运行边界测试，确认跨页题最终只有一条且包含两页字段与区域。

## T138：应用答案边界合并与连续边界

**文件：** `backend/homework_judge/recognition/boundary.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T137

**步骤：**
1. 使用答案规范化器处理跨页答案和解析。
2. 避免答案、解析和题干提示的重复拼接。
3. 按边界顺序更新工作草稿，使前次合并结果可进入下一边界。

**验证：** 运行边界测试，覆盖答案跨页、解析跨页和跨越两个边界的长内容。

## T139：集成边界调用到识别服务

**文件：** `backend/homework_judge/recognition/service.py`、`backend/homework_judge/recognition/boundary.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T134、T138

**步骤：**
1. 为主批次草稿分配稳定 `draft_id` 并保存批次归属。
2. 为每个相邻批次构造边界上下文和两张内联图片。
3. 对试卷和答案按页码顺序调用模型并立即应用合法决策。
4. 汇总边界调用 Token 用量。

**验证：** 使用模型桩运行识别服务测试，确认三个主批次产生两个边界调用且调用页码正确。

## T140：记录边界调用并实现局部降级

**文件：** `backend/homework_judge/recognition/service.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T139

**步骤：**
1. 为主批次和边界调用记录 `phase`、角色、索引、页码、原始响应和解析问题。
2. 捕获单个边界的超时、模型错误和非法响应，不终止其余边界。
3. 给受影响草稿增加 `boundary_merge_needs_review` 并记录安全错误摘要。

**验证：** 模拟第二个边界失败，确认主识别结果和第一个边界结果保留，第三个处理步骤继续。

## T141：完善最终整理和同号冲突标记

**文件：** `backend/homework_judge/recognition/consolidator.py`、`backend/tests/unit/test_consolidator.py`
**依赖：** T138

**步骤：**
1. 保留已实现的题号前缀、LaTeX/Unicode 公式规范化。
2. 移除只为旧重叠批次存在的临时批次去重依赖。
3. 对同号异题分别保留并增加稳定冲突异常。
4. 确保整理重复执行不增加或改变有效条目。

**验证：** 运行 `python -m pytest backend/tests/unit/test_consolidator.py -q -p no:cacheprovider`。

## T142：更新识别流水线与有效题匹配输入

**文件：** `backend/homework_judge/jobs/pipeline.py`、`backend/tests/integration/test_api_workflow.py`
**依赖：** T132、T140、T141

**步骤：**
1. 保存包含主批次和边界调用的原始运行记录与汇总用量。
2. 匹配阶段只查询 `is_duplicate = 0` 的题目。
3. 在匹配运行摘要中记录有效题数而非数据库总题数。

**验证：** 运行识别流水线集成用例，检查运行记录阶段类型、用量和匹配输入数量。

## T143：实现恢复题目的单题匹配建议

**文件：** `backend/homework_judge/matching/matcher.py`、`backend/tests/unit/test_matcher.py`
**依赖：** 无

**步骤：**
1. 提取可复用的单题候选评分逻辑。
2. 新增只使用未占用答案的单题建议入口。
3. 保持题号冲突、题干阈值和候选差距规则与全量匹配一致。
4. 断言调用不会修改其他匹配。

**验证：** 运行 `python -m pytest backend/tests/unit/test_matcher.py -q -p no:cacheprovider`。

## T144：实现统一下游失效服务

**文件：** `backend/homework_judge/review/__init__.py`、`backend/homework_judge/review/invalidation.py`、`backend/tests/unit/test_question_lifecycle.py`
**依赖：** T132

**步骤：**
1. 检查活跃学生提交和活跃评分状态并返回明确冲突。
2. 重置非活跃学生提交及题目区域状态。
3. 将相关评分运行设置 `is_stale = 1`，将当前生成物设置为 `stale`。
4. 保留旧学生响应、评分结果和文件记录。

**验证：** 运行生命周期测试，覆盖活跃阻断和非活跃失效两类路径。

## T145：实现标记重复事务

**文件：** `backend/homework_judge/review/lifecycle.py`、`backend/tests/unit/test_question_lifecycle.py`
**依赖：** T143、T144

**步骤：**
1. 查询题目和旧匹配并执行活跃运行保护。
2. 设置重复状态、待确认状态和排除匹配，释放答案条目。
3. 将任务退回 `review_pending` 并执行下游失效。
4. 写入包含旧匹配摘要的 `question_marked_duplicate` 审计事件。
5. 重复标记时直接返回当前状态且不重复写副作用。

**验证：** 生命周期测试确认题目记录仍存在、答案释放、任务重开、审计唯一且重复调用幂等。

## T146：实现恢复题目事务

**文件：** `backend/homework_judge/review/lifecycle.py`、`backend/tests/unit/test_question_lifecycle.py`
**依赖：** T145

**步骤：**
1. 恢复有效和待确认状态。
2. 基于当前未占用答案只为该题创建安全建议或未匹配记录。
3. 不恢复旧教师匹配，不覆盖其他题目的答案占用。
4. 执行下游失效并写 `question_restored` 审计事件。
5. 重复恢复保持幂等。

**验证：** 生命周期测试覆盖唯一答案建议、答案已占用、同号冲突和重复恢复。

## T147：接入重复题审核 API

**文件：** `backend/homework_judge/api/review.py`、`shared/contracts.ts`、`backend/tests/integration/test_api_workflow.py`
**依赖：** T145、T146

**步骤：**
1. 增加标记重复和恢复端点。
2. 审核详情返回全部题目及 `isDuplicate`。
3. 对重复题的编辑、区域修改、匹配修改、确认和重新打开操作返回明确冲突。
4. 保持统一成功和错误响应格式。

**验证：** 运行 API 集成测试，覆盖成功、404、重复调用和重复题操作保护。

## T148：统一有效题统计与完成校验

**文件：** `backend/homework_judge/api/tasks.py`、`backend/homework_judge/api/review.py`、`backend/homework_judge/jobs/pipeline.py`、`backend/tests/integration/test_api_workflow.py`
**依赖：** T147

**步骤：**
1. 最近任务题目总数和已确认数排除重复题。
2. 完成校验只检查有效题，但仍检查标记操作释放出的孤立答案。
3. 审核详情保证有效题与重复题统计口径一致。

**验证：** 集成测试确认标记后题数下降、答案变为未使用、完成按钮对应的服务端阻塞原因准确。

## T149：暴露评分结果过期状态

**文件：** `backend/homework_judge/api/grading.py`、`shared/contracts.ts`、`shared/schemas.ts`、`client/src/features/grading/GradingWorkspacePage.tsx`、`tests/ui/grading-workspace.test.tsx`
**依赖：** T132、T144

**步骤：**
1. 评分运行响应增加 `isStale`。
2. 前端对过期运行显示不可误认为当前结果的明确提示。
3. 保留旧结果查看能力并引导重新处理。

**验证：** 运行 `pnpm vitest run tests/ui/grading-workspace.test.tsx` 和相关评分 API 测试。

## T150：实现可访问的重复题确认框

**文件：** `client/src/features/review/ConfirmDuplicateQuestionDialog.tsx`、`client/src/styles.css`、`tests/ui/review-duplicate-question.test.tsx`
**依赖：** 无

**步骤：**
1. 显示题号、退出统计、释放答案和可恢复说明。
2. 支持键盘焦点、取消、确认和忙碌禁用。
3. 为所有操作提供可访问名称和文字状态。

**验证：** 运行 UI 测试，确认键盘打开、取消、确认及处理中不可重复提交。

## T151：实现审核页重复题筛选与操作

**文件：** `client/src/features/review/ReviewPage.tsx`、`client/src/styles.css`、`tests/ui/review-duplicate-question.test.tsx`
**依赖：** T147、T148、T150

**步骤：**
1. 普通筛选排除重复题，增加“重复题”筛选和数量。
2. 有效题操作栏接入确认框和标记 mutation。
3. 重复题采用只读显示并提供恢复 mutation。
4. 成功后刷新审核查询、清理消息并修正列表索引。

**验证：** UI 测试确认标记后题目退出普通列表、进入重复筛选、答案未使用数更新且恢复后回到待确认。

## T152：补齐审核页错误与禁用反馈

**文件：** `client/src/features/review/ReviewPage.tsx`、`client/src/styles.css`、`tests/ui/review-duplicate-question.test.tsx`
**依赖：** T151

**步骤：**
1. 显示 409 活跃运行阻断、网络失败和服务端校验消息。
2. 操作进行中禁用切换题目和重复提交。
3. 确保重复状态不只用颜色表达，并在 1280×720 布局内可操作。

**验证：** UI 测试覆盖错误响应、禁用状态、文字标签和焦点返回。

## T153：增加后端完整工作流测试

**文件：** `backend/tests/integration/test_api_workflow.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T142、T148

**步骤：**
1. 用模型桩构造跨页试卷题目和跨页答案解析。
2. 验证非重叠主批次、边界调用、最终题目、答案和匹配记录。
3. 标记漏网重复题、检查答案释放，再恢复并检查安全建议。
4. 验证原始调用记录和审计事件仍可查询。

**验证：** 运行相关后端集成测试，期望完整流程通过。

## T154：增加真实重复场景回归夹具

**文件：** `backend/tests/unit/test_consolidator.py`、`backend/tests/unit/test_boundary_reconciliation.py`
**依赖：** T141

**步骤：**
1. 将第 10 题公式格式差异和第 12 题跨页不完整输出整理为脱敏固定夹具。
2. 断言原 17 道题收敛为 15 道有效题。
3. 断言同号异题对照夹具仍保留两条并产生冲突异常。

**验证：** 运行两个识别整理测试文件，确认回归夹具通过且无需网络。

## T155：执行后端质量门禁

**文件：** 全部后端增量文件
**依赖：** T153、T154

**步骤：**
1. 运行完整 Python 测试。
2. 运行 Ruff、Mypy 和 Python 编译检查。
3. 修复增量导致的失败，不改动无关用户文件。

**验证：** `pnpm test:python`、`python -m ruff check backend`、`python -m mypy backend/homework_judge` 和 `python -m compileall backend/homework_judge` 全部通过。

## T156：执行前端质量门禁

**文件：** 全部前端增量文件
**依赖：** T149、T152

**步骤：**
1. 运行完整 Vitest 测试。
2. 运行 TypeScript 类型检查和生产构建。
3. 检查审核页普通、重复和空筛选状态。

**验证：** `pnpm test:ui`、`pnpm typecheck` 和 `pnpm build` 全部通过。

## T157：执行端到端与数据完整性验收

**文件：** `docs/acceptance-report.md`
**依赖：** T155、T156

**步骤：**
1. 使用跨页试卷和答案组合执行完整本地流程。
2. 记录主批次页码、边界调用数、最终题目数、答案数和匹配数。
3. 执行标记、筛选、恢复、完成校验和下游失效场景。
4. 比较操作前后原始文件摘要和原始运行响应，记录实际证据。

**验证：** 验收报告包含 AC63-AC78 的实际结果、命令或页面观察证据，且无未解释失败项。

### 执行顺序

```text
数据迁移：T131 → T132
识别配置：T133 → T134
边界核心：T133 → T135 → T136 → T137 → T138
识别集成：T134 + T138 → T139 → T140
最终整理：T138 → T141
流水线：T132 + T140 + T141 → T142

匹配：T143
失效：T132 → T144
生命周期：T143 + T144 → T145 → T146
API：T145 + T146 → T147 → T148
评分过期：T132 + T144 → T149

对话框：T150
审核页：T147 + T148 + T150 → T151 → T152

后端集成：T142 + T148 → T153
真实回归：T141 → T154
质量门禁：T153 + T154 → T155
            T149 + T152 → T156
最终验收：T155 + T156 → T157
```

建议按三个里程碑执行：

1. **M1 识别闭环：** T131-T142、T154 完成，非重叠识别与边界合并可用。
2. **M2 重复题闭环：** T143-T152 完成，标记、恢复、统计和下游失效可用。
3. **M3 正式验收：** T153-T157 完成，全部自动化与端到端证据齐备。

## 增量 Tasks：多空填空题评分配置初始化

### 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新建 | `backend/homework_judge/grading/blank_initialization.py` | 多空数量、答案拆分、分值和区域的确定性初始化 |
| 修改 | `backend/homework_judge/api/rubrics.py` | 查询配置时返回 saved、derived 或 none |
| 新建 | `backend/tests/unit/test_blank_initialization.py` | 初始化规则单元测试 |
| 修改 | `backend/tests/integration/test_grading_api.py` | 派生预览、保存和历史配置保护测试 |
| 修改 | `shared/contracts.ts` | 评分空位及初始化元数据契约 |
| 修改 | `client/src/features/grading/GradingConfigPanel.tsx` | 渲染后端派生空位与警告，移除固定 B1 |
| 新建 | `tests/ui/grading-config.test.tsx` | 多空配置 UI 与保存交互测试 |
| 修改 | `docs/acceptance-report.md` | 记录 AC79-AC89 验收证据 |

## T158：定义初始化数据结构与稳定警告

**文件：** `backend/homework_judge/grading/blank_initialization.py`
**依赖：** 无

**步骤：**
1. 定义初始化输入、空位数量信号、空位草稿、拆分结果和初始化结果结构。
2. 定义 saved/derived/none 之外的稳定警告代码与教师可读说明。
3. 保持模块无数据库和模型依赖。

**验证：** 导入模块并构造最小输入，类型检查和 Ruff 通过。

## T159：实现题干空位标记统计

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`
**依赖：** T158

**步骤：**
1. 识别连续半角/全角下划线及明确空白横线。
2. 排除单个 LaTeX 下划线和公式下标。
3. 返回稳定计数并增加中文题干和 LaTeX 对照测试。

**验证：** 运行标记统计单元测试，三个可见空得到 3，公式下标不增加数量。

## T160：实现强结构答案分组

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`
**依赖：** T158

**步骤：**
1. 识别 `(1)`、`（2）`、数字点号和圈号等答案序号。
2. 实现按编号、换行和分号的候选拆分。
3. 仅接受非空片段数等于预期空位数的候选。

**验证：** 测试编号答案、换行答案、分号答案和数量不匹配场景。

## T161：实现数学安全的受控空白拆分

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`
**依赖：** T160

**步骤：**
1. 在空白拆分前保护数学定界符、括号表达式和 LaTeX 命令。
2. 增加运算符边界、括号平衡和常见数值单位检查。
3. 支持科学计数法加文字答案，并拒绝拆分 `2 m/s` 和复杂公式。

**验证：** `1×10⁻⁶ 负` 安全拆成两段，数值单位和公式夹具返回歧义。

## T162：实现十进制默认分值分配

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`
**依赖：** T158

**步骤：**
1. 使用十进制数值向下量化基础分值。
2. 将余数分配给最后一空。
3. 校验空位数和满分的有效性。

**验证：** 断言 4 分三空得到 1.33、1.33、1.34，5 分两空得到 2.50、2.50。

## T163：实现区域排序、共享与数量选择

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`
**依赖：** T159、T160、T162

**步骤：**
1. 按页码、纵坐标和横坐标排序区域。
2. 实现题干标记、多个独立区域、强结构答案和单空兜底的数量优先级。
3. 实现区域一一分配、单复合区域共享和数量冲突警告。

**验证：** 覆盖三个独立区域、一个复合区域和区域/题干数量冲突测试。

## T164：组合初始化入口与真实示例回归

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`
**依赖：** T161、T163

**步骤：**
1. 组合空位数量、答案拆分、分值和区域结果。
2. 生成连续 B1、B2……及稳定警告。
3. 加入“失去 异种 吸引”“1×10⁻⁶ 负”和 `(1)电荷转移 遵守 (2)CD` 回归夹具。
4. 验证重复调用结果完全一致。

**验证：** 运行完整初始化单元测试，AC79-AC83 与幂等场景通过。

## T165：扩展后端评分配置查询

**文件：** `backend/homework_judge/api/rubrics.py`
**依赖：** T164

**步骤：**
1. 查询有效题干、满分、参考答案和答题区域并构造初始化输入。
2. 已保存空位返回 source=saved；未保存填空题调用初始化模块；非填空题返回 source=none。
3. 在响应中加入 signals、warnings 和派生空位，不在 GET 中写数据库。
4. 保持 PUT 请求与事务逻辑不变，并让保存后的响应返回 saved。

**验证：** API 模块类型检查通过，GET 前后数据库空位行数在未保存场景保持不变。

## T166：增加派生配置 API 集成测试

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T165

**步骤：**
1. 建立三个空、单复合区域和三段答案的未配置题夹具。
2. 断言 GET 返回 derived、三个空、正确答案和总分守恒。
3. 连续 GET 两次并断言结果一致且数据库仍无空位定义。

**验证：** 运行对应集成测试，AC79、AC83、AC87 通过。

## T167：增加保存、歧义和历史保护 API 测试

**文件：** `backend/tests/integration/test_grading_api.py`
**依赖：** T165

**步骤：**
1. 测试歧义答案返回正确空位数、空标准答案和检查警告。
2. 保存教师修改后的配置并断言后续 GET 返回 saved 且原样保持。
3. 为已有单空或多空配置修改题干和匹配答案，断言不会自动覆盖。
4. 验证单空和非填空题兼容行为。

**验证：** 运行评分配置 API 测试，AC82、AC84、AC85 通过。

## T168：补充共享 TypeScript 契约

**文件：** `shared/contracts.ts`
**依赖：** T165

**步骤：**
1. 定义评分空位、数量信号、初始化警告和评分配置响应接口。
2. 将 source 限定为 saved、derived、none。
3. 允许派生歧义空位具有空标准答案，但保持保存请求由后端严格校验。

**验证：** TypeScript `--noEmit` 检查通过且组件不再声明重复本地响应类型。

## T169：改造评分配置面板

**文件：** `client/src/features/grading/GradingConfigPanel.tsx`
**依赖：** T168

**步骤：**
1. 删除固定 `defaultBlank` 和整条答案写入 B1 的逻辑。
2. 直接使用 API 返回的空位列表和初始化元数据。
3. 显示“自动初始化尚未保存”、复合区域共享和答案需要检查等文字提示。
4. 保持空位增删、重排、编辑和保存交互，切题时清除上一题状态。

**验证：** 组件类型检查通过，多空数据能够渲染三个独立编辑行。

## T170：增加多空评分配置 UI 测试

**文件：** `tests/ui/grading-config.test.tsx`
**依赖：** T169

**步骤：**
1. 模拟 derived 三空响应并断言 B1-B3 的答案和分值可见。
2. 模拟歧义响应并断言警告、空答案字段和原参考答案仍可查看。
3. 使用键盘编辑各空并保存，断言 PUT 请求包含正确顺序和总分。
4. 模拟 saved 响应并断言不显示派生未保存提示。

**验证：** 运行新增 Vitest 文件，AC86 与主要前端场景通过。

## T171：执行后端质量门禁

**文件：** 全部后端增量文件
**依赖：** T166、T167

**步骤：**
1. 运行初始化、评分 API 和完整后端测试。
2. 运行 Ruff、Mypy 和 Python 编译检查。
3. 修复所有增量回归并记录实际通过数量。

**验证：** 后端全量 pytest、Ruff、Mypy 和 compileall 全部退出码为 0。

## T172：执行前端质量门禁与生产构建

**文件：** 全部前端增量文件
**依赖：** T170

**步骤：**
1. 运行新增及完整 Vitest 测试。
2. 运行 TypeScript 类型检查。
3. 执行生产构建并检查构建警告中无本次功能错误。

**验证：** 前端全量测试、TypeScript 检查和 Vite build 全部成功。

## T173：执行增量验收并记录证据

**文件：** `checklist.md`、`docs/acceptance-report.md`
**依赖：** T171、T172

**步骤：**
1. 按 AC79-AC89 检查多空、科学计数法、歧义、分值、历史配置和单空场景。
2. 走一遍打开派生配置、教师编辑保存、重新打开和逐空评分的组合流程。
3. 记录测试命令、通过数量、数据库前后行数和页面观察结果。

**验证：** 验收报告逐条映射 AC79-AC89，所有自动化条目通过；真实环境未执行项明确记录原因和影响。

### 执行顺序

```text
结构：T158
题干计数：T158 → T159
答案拆分：T158 → T160 → T161
分值：T158 → T162
区域与数量：T159 + T160 + T162 → T163
规则组合：T161 + T163 → T164

后端接口：T164 → T165 → T166 + T167 → T171
前端契约：T165 → T168 → T169 → T170 → T172
最终验收：T171 + T172 → T173
```

建议按三个里程碑执行：

1. **M1 确定性初始化：** T158-T164 完成，规则模块能够独立生成安全多空预览。
2. **M2 API 与界面闭环：** T165-T170 完成，教师能够查看、编辑、保存并重新读取多空配置。
3. **M3 质量与验收：** T171-T173 完成，全量回归和 AC79-AC89 证据齐备。

## 增量 Tasks：学生答卷自动批改、证据可见性与整页工作台

### 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `backend/homework_judge/observability.py` | JSON 日志、上下文和轮转配置 |
| 新建 | `backend/homework_judge/jobs/student_workflow.py` | 学生处理与自动批改协调器 |
| 新建 | `backend/homework_judge/api/grading_evidence.py` | 评分证据裁剪与安全预览 |
| 修改 | `backend/homework_judge/config.py`、`.env.example` | 日志配置项与默认值 |
| 修改 | `backend/homework_judge/db/database.py` | v9 迁移、运行版本字段和自动尝试表 |
| 修改 | `backend/homework_judge/main.py` | 日志初始化、HTTP 关联和工作流注册 |
| 修改 | `backend/homework_judge/jobs/manager.py` | 后台任务生命周期日志 |
| 修改 | `backend/homework_judge/jobs/student_pipeline.py` | 学生处理阶段日志和约束注释 |
| 修改 | `backend/homework_judge/jobs/grading_pipeline.py` | 版本化自动运行、后置复核和证据门禁 |
| 修改 | `backend/homework_judge/grading/contracts.py`、`audit.py` | 识别风险输入与全题证据审计 |
| 修改 | `backend/homework_judge/recognition/client.py`、`backend/homework_judge/artifacts/service.py` | 安全模型摘要与生成物日志 |
| 修改 | `backend/homework_judge/api/dependencies.py`、`submissions.py`、`grading.py`、`router.py` | 工作流依赖、自动状态、运行字段和证据路由 |
| 新建/修改 | `backend/tests/unit/test_observability.py`、`test_student_workflow.py`、`test_grading_evidence.py`、`test_grading_pipeline.py` | 后端单元回归 |
| 新建/修改 | `backend/tests/integration/test_auto_grading_workflow.py`、`test_student_submission_api.py`、`test_grading_api.py` | 自动串联与 API 集成 |
| 修改 | `shared/contracts.ts`、`client/src/lib/api.ts` | 自动状态、运行版本和证据预览契约 |
| 新建 | `client/src/features/grading/grading-progress.ts`、`GradingProgress.tsx` | 持久进度换算与组件 |
| 新建 | `client/src/features/grading/GradingEvidencePanel.tsx` | 可读证据卡片与定位 |
| 新建 | `client/src/features/grading/page-viewport.ts`、`usePageViewport.ts` | 整页、宽度和实际比例计算 |
| 修改 | `client/src/features/grading/GradingWorkspacePage.tsx`、`GradingPageOverlay.tsx` | 自动跳页、缩放、聚焦和高亮 |
| 修改 | `client/src/features/students/StudentSubmissionsPage.tsx`、`client/src/styles.css` | 自动进度与响应式布局 |
| 新建/修改 | `tests/ui/grading-progress.test.tsx`、`grading-evidence.test.tsx`、`grading-page-viewport.test.ts` 及现有评分/学生 UI 测试 | 前端行为回归 |
| 修改 | `checklist.md`、`docs/acceptance-report.md` | 最终验收证据 |

### 数据迁移与版本绑定

## T174：建立 v9 数据迁移测试

**文件：** `backend/tests/unit/test_database.py`
**依赖：** 无

**步骤：**
1. 从 v8 结构创建包含历史学生处理和评分运行的数据库夹具。
2. 定义升级后 `grading_runs.processing_revision_id`、`trigger_source` 和 `student_auto_grading_attempts` 的结构断言。
3. 增加同一处理版本重复自动运行必须违反唯一约束的断言。
4. 断言历史运行的新增字段保持兼容默认值并可正常读取。

**验证：** 仅运行新增迁移测试；在实现 T175 前测试失败原因只指向缺少 v9 结构。

## T175：实现 v9 迁移和自动尝试表

**文件：** `backend/homework_judge/db/database.py`
**依赖：** T174

**步骤：**
1. 将 schema 版本提升到 9，并为评分运行增加处理版本和触发来源字段。
2. 创建自动批改尝试表、外键、状态约束和查询索引。
3. 创建按学生提交与处理版本约束自动运行的部分唯一索引。
4. 为历史评分运行保留 `manual` 来源和空处理版本，不解析或重写旧快照。

**验证：** 运行 T174 迁移测试和完整数据库单元测试，升级可重复执行且退出码为 0。

### 日志基础设施

## T176：增加日志配置字段

**文件：** `backend/homework_judge/config.py`、`.env.example`、`backend/tests/unit/test_config.py`
**依赖：** 无

**步骤：**
1. 增加级别、控制台、文件、文件路径、单文件上限和备份数配置。
2. 对日志级别、字节上限和备份数执行范围校验。
3. 让相对日志路径以数据目录为根，默认使用 `logs/homework-judge.jsonl`。
4. 在示例环境文件中说明安全默认值，不加入真实密钥或本机路径。

**验证：** 运行配置单元测试，合法值被加载，非法级别和范围返回明确错误。

## T177：实现结构化日志与上下文

**文件：** `backend/homework_judge/observability.py`
**依赖：** T176

**步骤：**
1. 实现 JSON Lines formatter、控制台 handler 和有界轮转文件 handler。
2. 使用上下文变量保存请求及允许的业务标识。
3. 实现上下文绑定和白名单事件记录帮助函数。
4. 对异常写入类型、消息和堆栈；不序列化任意对象、请求体或模型内容。
5. 为字段白名单和上下文恢复原因补充文档字符串。

**验证：** 用临时目录初始化日志，分别写普通事件和异常事件，JSON 可解析且轮转 handler 配置正确。

## T178：增加日志格式、轮转与脱敏单元测试

**文件：** `backend/tests/unit/test_observability.py`
**依赖：** T177

**步骤：**
1. 断言请求、答卷、处理版本、运行和题目标识进入对应 JSON 字段。
2. 触发异常并断言日志包含堆栈。
3. 使用小文件上限触发轮转并断言备份数受限。
4. 将哨兵 API Key、认证头、姓名、学号、答案和模型原文作为非白名单值，断言日志文件中均不存在。

**验证：** 运行新日志测试，所有 JSON 行可解析且敏感哨兵搜索结果为空。

## T179：接入 HTTP 请求日志与异常堆栈

**文件：** `backend/homework_judge/main.py`、`backend/tests/integration/test_api_workflow.py`
**依赖：** T177

**步骤：**
1. 在应用生命周期初始化阶段配置日志。
2. 增加请求 ID 中间件，校验外部标识格式并在响应头回传。
3. 记录方法、路由模板、状态和耗时，不记录查询、请求头和请求体。
4. 为应用错误、参数错误和未处理异常增加分级日志；未处理异常使用堆栈记录。

**验证：** 集成测试发起成功、业务错误和未处理异常请求，断言响应 ID 与捕获日志关联且请求内容未泄露。

## T180：记录后台任务生命周期

**文件：** `backend/homework_judge/jobs/manager.py`、`backend/tests/unit/test_job_manager.py`
**依赖：** T177

**步骤：**
1. 记录任务创建、重复拒绝、取消、完成和失败事件。
2. 完成回调读取未捕获异常并写堆栈，然后安全移除当前任务。
3. 区分取消与失败，关闭管理器时不产生虚假错误。
4. 注释说明内存任务锁不是跨进程幂等事实来源。

**验证：** 运行任务管理器测试，正常、异常、重复和取消路径均有预期事件且没有“Task exception was never retrieved”。

## T181：增加学生处理阶段安全日志

**文件：** `backend/homework_judge/jobs/student_pipeline.py`
**依赖：** T177

**步骤：**
1. 在开始、配准完成、题框映射、识别完成、提交结果和失败处记录阶段事件。
2. 只记录标识、页数、题数、阻断计数、耗时和稳定错误代码。
3. 不记录姓名、学号、识别文本、图像内容或模型原始响应。
4. 为配准可靠性和 `recognition_needs_review` 不等于映射失败的状态分支增加原因注释。

**验证：** 运行学生流水线单元测试并捕获日志，阶段顺序正确且敏感夹具文本不存在。

## T182：增加模型调用和生成物安全日志

**文件：** `backend/homework_judge/recognition/client.py`、`backend/homework_judge/artifacts/service.py`
**依赖：** T177

**步骤：**
1. 模型客户端记录阶段、模型名、尝试次数、状态、耗时和用量摘要。
2. 禁止记录提示词、图片编码、Authorization 和原始响应。
3. 生成物服务记录类型、运行版本、开始、成功、失败和耗时。
4. 失败日志保留堆栈，但界面错误继续使用安全消息。

**验证：** 运行模型客户端和生成物相关测试，捕获日志包含摘要且哨兵提示词、响应和密钥不存在。

### 批改输入与证据门禁

## T183：扩展评分输入的识别风险字段

**文件：** `backend/homework_judge/grading/contracts.py`、`backend/tests/unit/test_grading_dependencies.py`
**依赖：** 无

**步骤：**
1. 为 `QuestionGradingInput` 增加识别需复核标志和稳定问题代码元组。
2. 保持现有构造调用默认兼容。
3. 校验问题代码为短非空字符串，不允许任意嵌套模型内容。

**验证：** 运行契约相关测试，旧输入仍可构造，新字段可序列化且非法代码被拒绝。

## T184：加强零证据题级审计

**文件：** `backend/homework_judge/grading/audit.py`、`backend/tests/unit/test_grading_audit.py`
**依赖：** T183

**步骤：**
1. 当题级结果没有任何有效证据时始终产生 `MISSING_EVIDENCE`。
2. 保留现有“得分项缺少引用”和“非满分缺少错误位置”检查。
3. 去重相同原因，避免同一道题产生多个相同开放复核项。

**验证：** 满分、零分和部分分的零证据结果都产生一个 `MISSING_EVIDENCE`，有证据结果无新增回归。

## T185：允许可复核识别进入评分输入

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/unit/test_grading_pipeline.py`
**依赖：** T183、T184

**步骤：**
1. 显式绑定并校验当前学生处理版本，接受 `ready` 和 `recognition_needs_review`。
2. 接受具有有效学生作答区域的 `needs_review` 响应，继续拒绝缺区域、失败和版本不一致响应。
3. 从学生响应提取稳定问题代码并填充评分输入。
4. 评分后合并 `LOW_RECOGNITION_CONFIDENCE`，再执行题级审计和复核项创建。
5. 注释说明为何识别风险可以后置、几何风险仍前置阻断。

**验证：** 运行评分流水线测试；低置信度题产生结果和复核项，映射失败、缺区域和旧版本仍被阻断。

## T186：版本化创建评分运行

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/homework_judge/api/grading.py`、`backend/tests/unit/test_grading_pipeline.py`
**依赖：** T175、T185

**步骤：**
1. `create_run` 接收处理版本和触发来源，并同时写顶层字段与不可变快照。
2. 自动来源发生唯一冲突时返回已有运行；手动接口保持活动运行冲突语义。
3. 运行列表与详情返回处理版本和触发来源。
4. 为数据库唯一约束作为重启幂等保护补充注释。

**验证：** 相同版本重复自动创建返回同一运行，新版本得到新运行，手动冲突测试保持原行为。

## T187：增加评分阶段结构化日志

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`
**依赖：** T177、T186

**步骤：**
1. 记录创建、预检、逐题汇总、审计、生成物转换、待复核、完成与失败事件。
2. 绑定提交、处理版本、运行和可选题目标识。
3. 逐题只记录题目标识、状态、耗时和复核原因代码，不记录答案或识别文本。

**验证：** 运行评分流水线测试并捕获日志，可按运行标识还原阶段顺序，敏感答案搜索为空。

### 自动串联工作流

## T188：实现自动批改尝试仓储与状态同步

**文件：** `backend/homework_judge/jobs/student_workflow.py`、`backend/tests/unit/test_student_workflow.py`
**依赖：** T175、T186

**步骤：**
1. 定义 `AutoGradingOutcome` 和按处理版本幂等创建尝试的内部操作。
2. 实现 pending、running、blocked、needs_review、completed、failed 状态转换。
3. 所有更新带处理版本条件，旧工作流不能覆盖新版本。
4. 将安全业务错误保存为代码和消息，未处理异常只向界面保存通用文本。

**验证：** 仓储单元测试覆盖初建、重复读取、状态同步和旧版本更新被拒绝。

## T189：实现自动批改协调器

**文件：** `backend/homework_judge/jobs/student_workflow.py`、`backend/tests/unit/test_student_workflow.py`
**依赖：** T188

**步骤：**
1. 读取当前处理版本并区分可评分、映射阻断和失败状态。
2. 对可评分版本幂等创建自动运行并等待评分流水线完成。
3. 将运行终态同步到自动尝试；创建前预检错误保存为 blocked。
4. 对取消进行向上传播，对异常记录关联堆栈。

**验证：** 使用假的评分流水线覆盖完成、待复核、阻断、失败和取消，状态与调用次数准确。

## T190：实现学生提交组合工作流

**文件：** `backend/homework_judge/jobs/student_workflow.py`、`backend/tests/unit/test_student_workflow.py`
**依赖：** T189

**步骤：**
1. 实现全量处理、人工配准后继续识别和历史当前版本补启动三个入口。
2. 学生处理成功后调用协调器，失败或映射待复核时只保存阻断状态。
3. 保持 `student:{submission_id}` 为整个组合任务的单一任务键。
4. 注释说明串行等待不会阻塞上传 API，只延长后台任务生命周期。

**验证：** 单元测试断言三个入口调用顺序、失败短路和自动补启动幂等。

## T191：注册组合工作流并替换学生 API 入口

**文件：** `backend/homework_judge/main.py`、`backend/homework_judge/api/dependencies.py`、`backend/homework_judge/api/submissions.py`
**依赖：** T179、T190

**步骤：**
1. 在应用状态注册协调器和组合工作流依赖。
2. 上传、重处理和人工配准继续识别改用对应工作流入口。
3. 保持返回 202、任务键、取消和上传失败清理行为。
4. 增加幂等自动补启动端点，历史已处理提交可显式恢复新流程。

**验证：** 运行学生提交 API 测试，后台协程指向组合工作流且现有错误码无回归。

## T192：在学生列表与详情暴露自动状态

**文件：** `backend/homework_judge/api/submissions.py`、`backend/tests/integration/test_student_submission_api.py`
**依赖：** T188、T191

**步骤：**
1. 按当前处理版本查询自动尝试及可选评分运行。
2. 返回状态、运行标识、阶段、题数进度、得分、待复核数和安全错误。
3. 历史视图按对应处理版本返回历史尝试，不混用当前运行。
4. 没有尝试的旧提交返回空兼容字段。

**验证：** API 测试覆盖处理中、阻断、待复核、完成和历史无尝试响应。

## T193：增加上传后自动批改集成测试

**文件：** `backend/tests/integration/test_auto_grading_workflow.py`
**依赖：** T191、T192

**步骤：**
1. 使用可控学生处理与评分依赖创建可自动评分提交。
2. 模拟上传后台任务并等待组合工作流结束。
3. 断言无需调用手动评分接口即可产生运行、逐题结果和生成物。
4. 断言运行绑定当前处理版本且触发来源为 automatic。

**验证：** 新集成测试完成 AC97 主路径，数据库只有一个自动尝试和一个自动运行。

## T194：增加低置信度后置复核集成测试

**文件：** `backend/tests/integration/test_auto_grading_workflow.py`、`backend/tests/unit/test_grading_pipeline.py`
**依赖：** T193

**步骤：**
1. 构造一题 `needs_review` 且证据有效、其他题正常的当前处理版本。
2. 断言整卷自动评分继续完成，风险题生成结果和开放复核项。
3. 构造零证据题并断言产生 `MISSING_EVIDENCE`。
4. 构造映射待复核版本并断言没有评分运行。

**验证：** AC98 三个分支通过，正常题结果数量不因风险题减少。

## T195：增加自动运行并发、重启与新版本测试

**文件：** `backend/tests/unit/test_student_workflow.py`、`backend/tests/integration/test_auto_grading_workflow.py`
**依赖：** T194

**步骤：**
1. 并发调用同一处理版本的自动入口并断言只创建一个运行。
2. 模拟内存任务状态丢失后再次调用，断言数据库唯一约束复用旧运行。
3. 创建新处理版本并断言产生新自动运行、旧运行变为过期。
4. 模拟旧工作流晚完成，断言不会覆盖新尝试状态。

**验证：** AC99 场景通过，无唯一约束泄漏为 500 错误。

### 证据预览与 API 契约

## T196：实现证据归属和坐标校验

**文件：** `backend/homework_judge/api/grading_evidence.py`、`backend/tests/unit/test_grading_evidence.py`
**依赖：** T175

**步骤：**
1. 按题级结果和区域标识从保存的顶层证据中精确选取记录。
2. 通过评分运行与学生页关联校验证据页属于同一提交。
3. 校验坐标为有限数、正尺寸并完全位于保存页面边界内。
4. 为不能使用客户端路径或坐标的安全原因补充文档字符串。

**验证：** 正常证据通过；未知区域、跨提交页、NaN、负尺寸和越界坐标返回稳定错误。

## T197：实现证据即时裁剪接口

**文件：** `backend/homework_judge/api/grading_evidence.py`、`backend/homework_judge/api/router.py`
**依赖：** T196

**步骤：**
1. 使用已验证学生页路径和证据坐标通过 Pillow 裁剪 RGB 图像。
2. 返回 JPEG、私有缓存头和由结果版本及坐标生成的 ETag。
3. 复用数据目录路径保护，文件缺失或图像损坏返回明确错误。
4. 注册证据路由，不接受文件路径或坐标查询参数。

**验证：** 对已知页面裁剪后像素尺寸与证据框一致，条件缓存和错误响应正确。

## T198：在题目详情附加证据预览信息

**文件：** `backend/homework_judge/api/grading.py`、`backend/tests/integration/test_grading_api.py`
**依赖：** T197

**步骤：**
1. 只在题目详情顶层证据加入 `previewUrl` 和保存页码。
2. 保持数据库证据 JSON、决策内证据和历史运行快照不可变。
3. 无法解析的历史证据保留原字段并附加兼容问题，不导致详情 500。

**验证：** API 详情含可访问预览地址，列表响应不膨胀，查询前后证据 JSON 完全相同。

## T199：增加证据接口安全集成测试

**文件：** `backend/tests/unit/test_grading_evidence.py`、`backend/tests/integration/test_grading_api.py`
**依赖：** T198

**步骤：**
1. 请求正常证据并验证媒体类型、尺寸和 ETag。
2. 尝试使用其他题结果的区域、其他提交页面和伪造路径。
3. 测试越界坐标、文件缺失、损坏图像和历史空证据。
4. 断言所有失败均不返回文件系统绝对路径。

**验证：** AC103 后端分支通过，数据目录外哨兵文件从未被读取。

### 前端契约与持久进度

## T200：补充自动状态和证据 TypeScript 契约

**文件：** `shared/contracts.ts`、`client/src/lib/api.ts`
**依赖：** T192、T198

**步骤：**
1. 定义自动批改尝试摘要及其运行进度。
2. 为评分运行增加处理版本和触发来源兼容字段。
3. 为题级证据增加预览地址和页码。
4. 保持历史响应字段可选，组件不得对旧记录使用非空断言。

**验证：** 运行 TypeScript `--noEmit`，API 客户端和现有夹具编译通过。

## T201：实现批改进度纯函数

**文件：** `client/src/features/grading/grading-progress.ts`、`tests/ui/grading-progress.test.tsx`
**依赖：** T200

**步骤：**
1. 定义各阶段固定基线和逐题 10%–85% 换算。
2. 对题数为零、题数越界和未知阶段执行安全限制。
3. 待复核与完成返回 100%，失败返回最后可信进度。
4. 注释说明阶段权重和“不提前显示 100%”约束。

**验证：** 纯函数测试覆盖全部状态、边界题数和单调结果。

## T202：实现持久可访问进度条组件

**文件：** `client/src/features/grading/GradingProgress.tsx`、`client/src/styles.css`
**依赖：** T201

**步骤：**
1. 渲染进度背景、当前阶段、题数/待复核数和百分比。
2. 增加 `progressbar` 语义与 `aria-valuemin/max/now`。
3. 为运行、待复核、完成和失败使用文字状态及非颜色提示。
4. 保持组件在终态可见，不由动画模拟未知进度。

**验证：** 用各状态夹具渲染组件，读屏名称、当前值和文字详情正确。

## T203：增加批改进度 UI 测试

**文件：** `tests/ui/grading-progress.test.tsx`
**依赖：** T202

**步骤：**
1. 测试排队、预检、逐题、审计、生成、待复核、完成和失败。
2. 断言逐题当前/总数及百分比。
3. 断言待复核为“自动批改完成，N 项待复核”。
4. 断言完成和失败后组件仍存在并具有正确可访问值。

**验证：** 新 Vitest 文件退出码为 0，覆盖 AC101。

### 前端证据可见性

## T204：实现证据与判分项关联帮助函数

**文件：** `client/src/features/grading/GradingEvidencePanel.tsx`、`tests/ui/grading-evidence.test.tsx`
**依赖：** T200

**步骤：**
1. 按区域标识把顶层证据映射到引用它的判分项。
2. 汇总标准答案、工具结论、原因、得分和复核原因。
3. 对未被具体判分项引用的有效证据标记为题级证据。
4. 对空证据返回稳定 `MISSING_EVIDENCE` 展示模型。

**验证：** 纯数据测试覆盖多判分项共享证据、题级证据和零证据。

## T205：实现批改证据卡片

**文件：** `client/src/features/grading/GradingEvidencePanel.tsx`
**依赖：** T204

**步骤：**
1. 渲染“批改证据”标题、学生识别、标准答案、规则、原因和得分。
2. 每个证据渲染预览图、页码、短区域标识和关联判分项。
3. 让卡片支持点击和键盘激活并回调页、区域。
4. 图片加载失败保留文字证据并显示错误；零证据显示恢复建议。

**验证：** 组件测试可通过角色和文字找到完整证据，图片失败后信息仍可读。

## T206：增加证据卡片与跨页定位测试

**文件：** `tests/ui/grading-evidence.test.tsx`
**依赖：** T205

**步骤：**
1. 渲染当前真实结构的一到多个证据卡片并断言字段齐全。
2. 键盘激活第二页证据并断言定位回调参数。
3. 测试无预览地址的历史证据、图片失败和完全空证据。
4. 断言状态不只依赖蓝框或颜色表达。

**验证：** 新证据 UI 测试通过 AC102、AC103 的组件分支。

### 整页视口与防误滑

## T207：实现页面视口纯计算

**文件：** `client/src/features/grading/page-viewport.ts`、`tests/ui/grading-page-viewport.test.ts`
**依赖：** 无

**步骤：**
1. 实现整页适配、宽度适配和实际像素三种基准比例。
2. 将缩放限制在 25%–300%，返回画布尺寸和是否溢出。
3. 处理零尺寸容器、非法页面尺寸和浮点舍入。
4. 注释说明整页比例必须同时受宽高约束。

**验证：** 以 1697×2400 页面和三种目标视口断言整页不超宽高、宽度模式可纵向溢出。

## T208：实现视口尺寸监听 Hook

**文件：** `client/src/features/grading/usePageViewport.ts`
**依赖：** T207

**步骤：**
1. 使用 `ResizeObserver` 监听查看区内容尺寸。
2. 调用纯函数返回当前模式和缩放下的画布尺寸。
3. 在测试环境或浏览器不支持观察器时提供安全初值和窗口调整兜底。
4. 卸载时断开观察器，避免跨页面残留回调。

**验证：** Hook 组件测试模拟尺寸变化，画布宽高随容器更新且观察器被清理。

## T209：让试卷覆盖层使用显式尺寸和当前证据高亮

**文件：** `client/src/features/grading/GradingPageOverlay.tsx`、`client/src/styles.css`
**依赖：** T208

**步骤：**
1. 接收计算后的画布宽高，不再由固定 `width: min(100%, 850px)` 决定。
2. 接收当前证据区域并增加明显的选中轮廓和文字说明。
3. 保持 SVG viewBox 为原图坐标，绘制点继续换算到原始页面像素。
4. 绘制模式保持 pointer capture、最小框和触控禁用。

**验证：** 现有覆盖层测试坐标不变，新增测试断言画布尺寸和选中证据类名。

## T210：增加视口、缩放和滚轮回归测试

**文件：** `tests/ui/grading-page-viewport.test.ts`、`tests/ui/grading-review-report.test.tsx`
**依赖：** T209

**步骤：**
1. 覆盖三种模式、缩放边界和 ResizeObserver 重算。
2. 触发普通滚轮并断言不调用翻页或缩放状态变更。
3. 进入绘制模式触发 pointer 事件，断言仍只提交原图坐标框。
4. 检查整页模式的计算画布不产生纵向溢出。

**验证：** 视口和现有评分 UI 测试通过，AC104、AC105 的逻辑分支覆盖。

### 工作台与学生列表布局

## T211：接入证据默认显示与题目自动跳页

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`
**依赖：** T205、T209

**步骤：**
1. 将识别证据图层默认设为开启。
2. 题目详情变化后优先定位首证据页，否则定位已捕获题框页。
3. 维护当前证据区域，卡片选择时切页并高亮。
4. 切题时重置到新题首证据，但保留用户图层偏好。

**验证：** 跨页题目 UI 测试断言页码自动变化、证据复选框默认选中且高亮正确。

## T212：接入持久进度和证据详情区

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`
**依赖：** T202、T211

**步骤：**
1. 对所有已有运行始终渲染 `GradingProgress`。
2. 将证据面板放在右侧判分说明之前。
3. 自动运行处理中允许进入工作台观察，手动启动只作为历史兼容入口。
4. 自动运行阻断或失败显示阶段、代码和恢复操作。

**验证：** 运行中、待复核、完成和失败夹具都能同时看到进度与相应证据/诊断。

## T213：实现查看模式工具栏与聚焦状态

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`
**依赖：** T210、T212

**步骤：**
1. 增加整页、宽度、100%、放大、缩小和聚焦按钮。
2. 显示当前模式与比例，禁用超过边界的缩放按钮。
3. 聚焦模式折叠两侧栏但不卸载查询或清除题目、页码、图层和证据选择。
4. 翻页只保留明确按钮与键盘快捷键，不绑定滚轮。

**验证：** UI 测试操作全部按钮，状态文字、边界禁用和聚焦前后选择保持正确。

## T214：改造三栏、聚焦和窄屏样式

**文件：** `client/src/styles.css`
**依赖：** T213

**步骤：**
1. 让查看区以可用宽高承载显式画布，整页模式居中且无内部纵向滚动。
2. 增加侧栏折叠、聚焦模式、证据卡片和选中证据样式。
3. 小于 1200px 时切换为单主面板而非压缩三栏。
4. 清除页面主体与中央画布竞争的纵向滚动，保留右侧详情独立滚动。

**验证：** 在 1366×768、1440×900 和 1920×1080 样式夹具/浏览器中，整页画布宽高均不超过可用区域。

## T215：在学生列表显示自动批改进度

**文件：** `client/src/features/students/StudentSubmissionsPage.tsx`、`client/src/styles.css`
**依赖：** T192、T200、T202

**步骤：**
1. 上传按钮改为“上传并自动批改”。
2. 当前提交有未终结自动尝试或运行时继续轮询。
3. 列表项显示处理阶段、评分阶段、小型进度、得分和待复核数。
4. 自动运行存在时显示“查看批改进度/结果”；阻断显示明确原因，历史无尝试保留手动入口。

**验证：** 学生页 UI 测试覆盖处理中、批改中、阻断、待复核、完成和历史兼容状态。

## T216：更新现有评分与学生 UI 回归测试

**文件：** `tests/ui/grading-review-report.test.tsx`、`tests/ui/grading-workspace.test.tsx`、`tests/ui/student-alignment-review.test.tsx`、`tests/ui/student-overlay.test.tsx`
**依赖：** T212、T214、T215

**步骤：**
1. 更新证据默认开启、持久进度和上传按钮文案断言。
2. 保持教师复核、错误位置绘制、生成物预览和人工配准测试。
3. 增加聚焦模式、跨页自动定位和自动运行导航断言。
4. 修正夹具新增可选字段，不放宽核心行为断言。

**验证：** 所有现有和新增 Vitest 文件通过，无快照式掩盖回归。

### 注释、安全与最终门禁

## T217：执行复杂逻辑注释审查

**文件：** 本轮修改的后端状态机、证据 API、进度和视口文件
**依赖：** T187、T195、T199、T213

**步骤：**
1. 检查自动门禁、数据库幂等、旧版本保护、证据坐标和进度权重是否有原因注释。
2. 检查 SVG 原图坐标与 CSS 画布尺寸分离是否有明确注释。
3. 删除复述赋值、分支或样式名称的噪声注释。
4. 确保文档字符串与实际接口、状态和约束一致。

**验证：** 按 AC107 代码审查清单逐文件通过，搜索不到本轮遗留 TODO/TBD。

## T218：执行日志隐私与关联集成检查

**文件：** `backend/tests/integration/test_auto_grading_workflow.py`、`backend/tests/unit/test_observability.py`
**依赖：** T179、T182、T187、T195

**步骤：**
1. 用包含哨兵密钥、认证头、姓名、学号、识别答案和标准答案的完整自动流程运行测试。
2. 断言日志能按请求、提交、处理版本和运行标识串联。
3. 扫描捕获日志和轮转文件，断言所有敏感哨兵不存在。
4. 模拟生成物异常并断言关联堆栈存在。

**验证：** AC100、AC106 日志分支通过，敏感扫描零命中。

## T219：执行后端完整质量门禁

**文件：** 全部后端增量文件
**依赖：** T199、T218

**步骤：**
1. 运行新增迁移、日志、工作流、评分和证据测试。
2. 运行完整后端 pytest。
3. 运行 Ruff、Mypy 和 Python compileall。
4. 修复所有增量与现有回归并记录实际通过数量。

**验证：** 后端全量测试、Ruff、Mypy 和 compileall 全部退出码为 0。

## T220：执行前端完整质量门禁与生产构建

**文件：** 全部前端增量文件
**依赖：** T216、T217

**步骤：**
1. 运行进度、证据、视口及完整 Vitest。
2. 运行 TypeScript 类型检查。
3. 执行 Vite 生产构建。
4. 检查构建输出不含本轮功能警告或缺失资源。

**验证：** 前端全量测试、类型检查和生产构建全部退出码为 0。

## T221：执行桌面视口与防误滑验收

**文件：** `checklist.md`、`docs/acceptance-report.md`
**依赖：** T220

**步骤：**
1. 在 1366×768、1440×900 和 1920×1080 打开同一多页答卷。
2. 记录整页模式的查看区和画布尺寸，确认无需纵向滑动画布。
3. 检查宽度、100%、缩放、聚焦、翻页、跨页证据和绘制模式。
4. 连续使用鼠标滚轮和触控板，确认不误翻页或缩放。

**验证：** AC104、AC105 每个视口均有实际观察记录；受环境限制的触控板项明确标注而不虚报。

## T222：执行上传到自动批改端到端验收

**文件：** `checklist.md`、`docs/acceptance-report.md`
**依赖：** T219、T221

**步骤：**
1. 上传可靠学生答卷，不点击手动评分，观察处理和批改进度。
2. 打开工作台检查整页、持久进度、每题证据卡片和原图定位。
3. 对风险题完成后置复核并检查批注试卷和错题报告更新。
4. 检查数据库运行版本、自动尝试、日志关联和生成物状态。

**验证：** AC97-AC109 逐条记录命令、数据库计数、页面观察和文件结果；所有未通过项先修复并重跑。

### 执行顺序

```text
迁移：T174 → T175
日志：T176 → T177 → T178 + T179 + T180 + T181 + T182

评分输入：T183 → T184 → T185 → T186 → T187
自动工作流：T175 + T186 → T188 → T189 → T190 → T191 → T192 → T193 → T194 → T195

证据 API：T175 → T196 → T197 → T198 → T199

前端契约：T192 + T198 → T200
进度：T200 → T201 → T202 → T203
证据 UI：T200 → T204 → T205 → T206
视口：T207 → T208 → T209 → T210
工作台：T205 + T209 → T211 → T212 → T213 → T214
学生列表：T192 + T200 + T202 → T215
前端回归：T212 + T214 + T215 → T216

注释与隐私：T187 + T195 + T199 + T213 → T217；T179 + T182 + T187 + T195 → T218
质量门禁：T199 + T218 → T219；T216 + T217 → T220
最终验收：T220 → T221；T219 + T221 → T222
```

建议按五个里程碑执行：

1. **M1 可观测基础：** T174-T182，数据库升级和安全日志可独立验证。
2. **M2 自动批改闭环：** T183-T195，上传后自动运行、后置复核和幂等完成。
3. **M3 证据可信展示：** T196-T206，证据裁剪、契约和可读卡片完成。
4. **M4 整页工作台：** T207-T216，持久进度、整页适配、聚焦和学生列表完成。
5. **M5 质量与验收：** T217-T222，注释、隐私、全量门禁和端到端证据齐备。
