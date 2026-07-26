# 双科目答案自动配置功能 Tasks（Node/TypeScript v1 归档）

> 实施状态（2026-07-26）：T1–T22 已完成，类型检查、26 项自动化测试、生产构建、隔离生产冒烟和 1280px 页面检查均通过。会产生费用的真实百炼试卷调用作为人工验收项保留在 `checklist.md`。

## 文件清单

| 操作 | 文件或目录 | 职责 |
| --- | --- | --- |
| 修改 | `shared/contracts.ts` | 科目、题型、答案模式、版本、草稿、运行与来源契约 |
| 修改 | `shared/schemas.ts` | 创建任务、自动输出、教师审核和发布校验 |
| 新建 | `shared/subject-profiles.ts` | 初中数学与高中物理规则 |
| 新建 | `server/src/db/migrations.ts` | SQLite 版本迁移执行器 |
| 新建 | `server/src/db/migrations/002-answer-config.sql` | 新表、字段和数据迁移 |
| 修改 | `server/src/db/database.ts`、`schema.sql` | 接入迁移和最新全量模式 |
| 新建 | `server/src/db/repositories/answer-config.ts` | 版本、草稿、审核和发布数据访问 |
| 新建 | `server/src/db/repositories/answer-runs.ts` | 自动运行和搜索来源数据访问 |
| 修改 | `server/src/db/repositories/tasks.ts`、`submissions.ts` | 科目、版本和准入数据 |
| 修改 | `server/src/files/storage.ts` | 参考答案文件类型与双文件上传 |
| 新建 | `server/src/answer-config/prompts.ts` | 双科目提取、搜索与生成提示词 |
| 新建 | `server/src/answer-config/output.ts` | 自动结果解析与校验 |
| 新建 | `server/src/answer-config/extractor.ts` | 试卷及参考答案视觉提取 |
| 新建 | `server/src/model/dashscope-search.ts` | 百炼原生联网搜索 |
| 新建 | `server/src/model/answer-generator.ts` | 单题模型解题 |
| 新建 | `server/src/answer-config/resolver.ts` | 搜索优先与生成回退 |
| 新建 | `server/src/answer-config/orchestrator.ts` | 答案配置队列与状态机 |
| 新建 | `server/src/answer-config/publisher.ts` | 教师审核版本发布 |
| 新建 | `server/src/api/answer-config.ts` | 进度、审核、重试、来源和发布 API |
| 修改 | `server/src/api/tasks.ts`、`submissions.ts`、`grading.ts` | 新任务输入与学生批改准入 |
| 修改 | `server/src/app.ts`、`index.ts`、`config.ts` | 注册模块、配置和编排器 |
| 修改 | `server/src/grading/*`、`reports/statistics.ts` | 答案版本与高中物理评分 |
| 新建 | `client/src/features/answer-config/*` | 答案处理进度与教师审核工作台 |
| 修改 | `client/src/features/tasks/CreateTaskPage.tsx` | 科目、答案方式和参考答案上传 |
| 修改 | `client/src/features/submissions/UploadPage.tsx` | 答案未发布时阻止学生上传 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 科目与答案版本展示 |
| 修改 | `client/src/app/AppShell.tsx`、`main.tsx` | 答案审核导航和路由 |
| 修改 | `client/src/lib/api.ts`、`styles.css` | API 客户端与视觉样式 |
| 新建/修改 | `tests/answer-config/*`、`tests/api/*`、`tests/model/*`、`tests/ui/*` | 自动化验收 |
| 修改 | `.env.example`、`README.md` | 搜索配置、使用流程和部署说明 |

## T1：扩展共享领域模型

**文件：** `shared/contracts.ts`、`shared/subject-profiles.ts`

**依赖：** 无

**步骤：**

1. 把科目改为初中数学和高中物理枚举。
2. 增加 `calculation` 题型及各科允许题型映射。
3. 定义答案模式、配置状态、版本、草稿、来源、运行和进度类型。
4. 扩展任务、正式题目、学生提交和报告契约中的答案版本字段。
5. 实现按科目获取题型、提取说明和评分点规则的共享入口。

**验证：** 运行 `npm run typecheck`，共享类型可被前后端导入，非法科目和题型组合无法通过静态类型。

## T2：建立请求与模型输出校验

**文件：** `shared/schemas.ts`、`server/src/answer-config/output.ts`

**依赖：** T1

**步骤：**

1. 扩展任务创建模式，校验科目、答案模式和参考答案要求。
2. 定义整卷识别、参考答案提取、搜索答案和模型生成输出模式。
3. 定义教师草稿修改、退回原因和发布请求模式。
4. 校验题号唯一、题型符合科目、分数非负、评分点总分不超满分。
5. 校验搜索来源必须为 HTTP(S) URL，并限制标题与摘要长度。

**验证：** 运行模式测试，覆盖两个科目、四种题型、缺题、重复题号、非法 URL 和越界分数。

## T3：实现 SQLite 迁移执行器

**文件：** `server/src/db/migrations.ts`、`server/src/db/database.ts`

**依赖：** T1

**步骤：**

1. 读取和写入 `PRAGMA user_version`。
2. 按版本顺序加载迁移并在事务中执行。
3. 初始化新数据库时直接建立最新模式。
4. 迁移失败时回滚并抛出可诊断错误。
5. 为测试数据库暴露相同初始化路径。

**验证：** 对空数据库和旧模式数据库分别初始化两次，版本号正确且幂等。

## T4：新增答案配置数据表并迁移旧数据

**文件：** `server/src/db/schema.sql`、`server/src/db/migrations/002-answer-config.sql`

**依赖：** T3

**步骤：**

1. 扩展任务、文件、题目和提交表。
2. 新建答案版本、答案草稿、解析运行和搜索来源表。
3. 增加任务版本号、题号唯一性和常用状态索引。
4. 为现有数学任务和题目创建已批准初始版本。
5. 将已有学生提交绑定到对应初始版本。
6. 保持原有外键、模型运行和教师复核数据完整。

**验证：** 运行迁移测试，对迁移前后关键表逐项计数并验证旧报告仍可读取。

## T5：实现答案配置与运行仓储

**文件：** `server/src/db/repositories/answer-config.ts`、`answer-runs.ts`

**依赖：** T4

**步骤：**

1. 实现版本创建、详情、进度和状态更新。
2. 实现草稿整批创建、单题读取、教师修改和审核状态转换。
3. 自动字段与教师字段分开读写。
4. 实现运行开始、成功、失败和原始响应持久化。
5. 实现搜索来源写入、排序和安全读取。
6. 为每个状态变化写入审计事件。

**验证：** 仓储测试走完“自动草稿 → 教师修改 → 通过 → 重试历史保留”状态链。

## T6：扩展文件和任务创建

**文件：** `server/src/files/storage.ts`、`server/src/api/tasks.ts`、`server/src/db/repositories/tasks.ts`

**依赖：** T2、T4

**步骤：**

1. 允许 `reference_answer` 文件类型并建立独立目录。
2. 将任务创建上传改为 `template` 与可选 `referenceAnswer` 字段。
3. 校验参考答案模式必须上传参考答案，无参考答案模式不能误绑定文件。
4. 同一事务保存任务和两个文件记录。
5. 任何校验或数据库错误都清理本次临时文件和孤立文件。
6. 任务详情返回科目、答案模式、配置状态和版本摘要。

**验证：** API 测试覆盖两种创建模式、缺少参考答案、非法文件和中途失败清理。

## T7：实现双科目提示词和整卷输出解析

**文件：** `server/src/answer-config/prompts.ts`、`output.ts`

**依赖：** T1、T2

**步骤：**

1. 编写试卷提取提示词，只接受所属科目支持题型。
2. 编写参考答案匹配提示词，明确不得强行匹配缺失题。
3. 编写数学简答与物理计算题评分点规则。
4. 要求模型返回结构化 JSON 和置信度，不输出隐藏思维过程。
5. 把低置信度、缺题和类型冲突转为需关注状态。

**验证：** 固定模型输出夹具能解析；未知题型、重复题号和非法分值被拒绝。

## T8：实现视觉题目提取器

**文件：** `server/src/answer-config/extractor.ts`、`server/src/model/dashscope.ts`

**依赖：** T5、T7

**步骤：**

1. 复用图片标准化与 PDF 逐页转换。
2. 构造模板试卷多页视觉输入。
3. 参考答案模式追加区分明确的参考答案页面。
4. 调用前创建运行记录，响应后先保存原始内容再解析。
5. 返回整卷草稿；失败时保存错误类型和可重试状态。

**验证：** 假视觉模型分别返回完整、缺题和非法 JSON，运行记录与草稿状态正确。

## T9：实现百炼原生联网搜索客户端

**文件：** `server/src/model/dashscope-search.ts`、`server/src/config.ts`、`.env.example`

**依赖：** T2、T5

**步骤：**

1. 增加原生 Base URL、搜索模型、置信度阈值和并发配置。
2. 构造只包含科目与单题题干的 Generation 请求。
3. 启用搜索和来源返回，保存去敏请求快照。
4. 解析回答、`search_info`、来源和 Token 用量。
5. 无来源、无直接答案或低置信度时返回 `not_found`。
6. 分类处理鉴权、限流、超时、服务错误和非法响应。

**验证：** 假百炼服务断言请求不含个人信息，覆盖搜索成功、无来源、低置信度、限流和超时。

## T10：实现单题模型生成器

**文件：** `server/src/model/answer-generator.ts`、`server/src/answer-config/prompts.ts`

**依赖：** T2、T7

**步骤：**

1. 根据 SubjectProfile 构造单题解题请求。
2. 返回答案、评分点、简要依据和置信度。
3. 物理计算题校验公式、步骤和单位类评分点。
4. 数学简答题校验关键步骤评分点。
5. 保存原始响应、解析结果、用量和失败信息。

**验证：** 两科固定输出均通过；缺失物理单位规则和越界评分点时标为需关注。

## T11：实现搜索优先与模型回退

**文件：** `server/src/answer-config/resolver.ts`

**依赖：** T9、T10

**步骤：**

1. 参考答案模式直接使用提取结果，不静默联网补全。
2. Agent 模式逐题调用搜索客户端。
3. 搜索成功时保存来源并标记 `web_searched`。
4. 搜索 `not_found` 时调用生成器并标记 `model_generated`。
5. 保持搜索和生成两次运行记录可独立查看。
6. 将单题失败隔离为 `failed` 草稿。

**验证：** 解析器测试覆盖搜索命中、搜索未命中后生成、搜索异常和生成异常。

## T12：实现答案配置编排器

**文件：** `server/src/answer-config/orchestrator.ts`

**依赖：** T5、T8、T11

**步骤：**

1. 创建新草稿版本并设置任务状态。
2. 执行整卷提取并创建逐题草稿。
3. 按配置并发逐题解析答案。
4. 持续刷新识别、搜索、生成、需关注和失败进度。
5. 实现单题重搜和直接重生成队列。
6. 防止同题重复入队。
7. 启动时恢复遗留处理中状态为可重试失败。

**验证：** 五题并发测试断言活跃数不超上限，成功和失败不互相阻塞。

## T13：实现教师审核与版本发布

**文件：** `server/src/answer-config/publisher.ts`、`server/src/db/repositories/answer-config.ts`

**依赖：** T5、T12

**步骤：**

1. 保存教师字段时撤销该题审核状态。
2. 单题通过前校验有效答案、题型、满分和评分点。
3. 退回时保存教师原因。
4. 发布前检查所有题均已通过且题号唯一。
5. 在事务内创建正式题目、批准版本、切换活动版本和写审计。
6. 修改已发布配置时派生新草稿，不修改旧正式题目。

**验证：** 发布测试覆盖成功、未审核阻塞、重复题号回滚和旧版本保持不变。

## T14：实现答案配置 API

**文件：** `server/src/api/answer-config.ts`、`server/src/app.ts`、`server/src/index.ts`

**依赖：** T12、T13

**步骤：**

1. 注册生成、详情和进度接口。
2. 注册教师修改、通过、退回、重搜和重生成接口。
3. 注册整卷发布和运行详情接口。
4. 使用统一错误响应和 Zod 校验。
5. 在服务入口创建并注入搜索、生成和答案编排实例。
6. 保证所有响应不包含 Key 或授权头。

**验证：** Supertest 完整走通两种答案模式的 API 流程。

## T15：为学生批改绑定答案版本

**文件：** `server/src/api/submissions.ts`、`grading.ts`、`server/src/db/repositories/submissions.ts`、`server/src/grading/orchestrator.ts`

**依赖：** T13

**步骤：**

1. 上传学生试卷前检查当前答案版本已批准。
2. 创建提交时固定写入版本 ID。
3. 批改时按提交版本读取正式题目。
4. 新草稿存在时阻止上传和启动新批改。
5. 保证旧提交继续使用旧版本。
6. 错误响应明确指向答案审核页面。

**验证：** API 测试覆盖未发布阻塞、发布后允许、新版本草稿暂停和旧提交可读。

## T16：扩展高中物理学生批改

**文件：** `server/src/grading/prompt.ts`、`output.ts`、`reviews.ts`、`server/src/reports/statistics.ts`

**依赖：** T1、T15

**步骤：**

1. 批改提示词按科目选择规则。
2. 支持 `calculation` 类型。
3. 物理计算题按公式、代入、结果和单位评分。
4. 报告与统计返回科目和答案版本。
5. 保持数学已有流程与数据兼容。

**验证：** 数学回归测试通过；新增物理计算题初评、复核和报告测试通过。

## T17：扩展前端 API 和路由

**文件：** `client/src/lib/api.ts`、`client/src/main.tsx`、`client/src/app/AppShell.tsx`

**依赖：** T14

**步骤：**

1. 添加答案配置详情、进度、审核、重试、发布和运行详情请求。
2. 新增答案审核路由。
3. 在任务导航中增加答案配置状态和入口。
4. 为生成中状态配置轮询，结束后自动停止。
5. 统一显示后端字段错误和业务错误。

**验证：** 前端类型检查通过，路由可从任务页访问且无未处理请求类型。

## T18：改造任务创建页面

**文件：** `client/src/features/tasks/CreateTaskPage.tsx`、`client/src/styles.css`

**依赖：** T6、T17

**步骤：**

1. 增加科目卡片选择。
2. 增加答案模式选择。
3. 参考答案模式显示第二个上传区。
4. 创建成功后自动启动答案配置并进入进度页面。
5. 按科目显示支持题型和不支持范围。
6. 移除以手工逐题录入为主的旧创建步骤。

**验证：** UI 测试完成两种模式创建，缺少必需文件时显示字段级提示。

## T19：实现答案审核工作台

**文件：** `client/src/features/answer-config/*`、`client/src/styles.css`

**依赖：** T17

**步骤：**

1. 实现原卷预览与逐题草稿双栏页面。
2. 实现处理进度和状态汇总。
3. 实现状态与来源筛选。
4. 实现题型、满分、答案和评分点编辑。
5. 实现通过、退回、重搜和重生成操作。
6. 展示搜索来源和只读运行历史。
7. 所有题通过时启用发布按钮。
8. 未保存编辑切换题目时给出提示。

**验证：** 组件测试覆盖修改、筛选、来源展示、操作按钮和发布阻塞。

## T20：调整学生上传、复核与报告页面

**文件：** `client/src/features/submissions/UploadPage.tsx`、`review/ReviewPage.tsx`、`reports/ReportsPage.tsx`

**依赖：** T15、T16、T17

**步骤：**

1. 未批准答案时显示阻塞卡片和审核入口。
2. 已批准时显示答案版本和批准时间。
3. 学生复核页显示科目与版本。
4. 物理计算题以分步评分形式展示。
5. 报告显示所用答案版本，旧报告保持可访问。

**验证：** UI 测试覆盖阻塞、批准后上传和物理报告展示。

## T21：补齐端到端与安全测试

**文件：** `tests/answer-config/*`、`tests/api/answer-config-workflow.test.ts`、`tests/model/dashscope-search.test.ts`、`tests/ui/answer-config.test.tsx`

**依赖：** T1–T20

**步骤：**

1. 完成有参考答案的生成、审核、发布和学生批改流程。
2. 完成无参考答案的搜索命中流程。
3. 完成搜索未命中后模型生成流程。
4. 覆盖单题重试、重启恢复和不可变旧版本。
5. 搜索请求断言不含个人信息。
6. 对响应、数据库和构建产物执行测试 Key 片段搜索。
7. 回归原有数学批改、教师终评和统计流程。

**验证：** `npm test` 全部通过且没有未关闭句柄。

## T22：更新文档并完成生产验收

**文件：** `.env.example`、`README.md`、`checklist.md`

**依赖：** T21

**步骤：**

1. 更新双科目、答案模式和教师审核说明。
2. 记录百炼视觉、原生搜索模型和 Base URL 配置。
3. 说明数据迁移、备份和失败恢复。
4. 执行类型检查、测试和生产构建。
5. 启动生产包检查健康接口、SQLite 和 React 页面。
6. 对创建、答案审核、上传、学生复核和报告页面执行桌面视觉检查。

**验证：** `npm run typecheck`、`npm test`、`npm run build` 均通过，生产烟雾测试返回 200。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6
          ├────────→ T7 → T8
          └────────→ T9 → T10
T8 + T9 + T10 → T11 → T12 → T13 → T14
T13 → T15 → T16
T14 → T17 → T18 → T19
T15 + T16 + T17 → T20
T1–T20 → T21 → T22
```
