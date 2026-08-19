# 前端操作闭环与复核后错题报告 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `client/src/components/ActionFeedback.tsx` | 统一展示操作状态、错误与禁用原因 |
| 修改 | `client/src/styles.css` | 增加反馈、忙碌和禁用原因样式 |
| 修改 | `client/src/lib/api.ts` | 补齐复核响应类型与生成物错误处理 |
| 修改 | `shared/contracts.ts` | 对齐复核响应和运行/生成物契约（如现有字段不足） |
| 修改 | `client/src/features/tasks/TaskListPage.tsx` | 删除、导航和加载失败闭环 |
| 修改 | `client/src/features/tasks/CreateTaskPage.tsx` | 上传忙碌、校验和失败恢复 |
| 修改 | `client/src/features/processing/ProcessingPage.tsx` | 重试忙碌与异常提示 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 保存、确认、取消、忽略、完成及阻塞原因 |
| 修改 | `client/src/features/students/StudentSubmissionsPage.tsx` | 上传、补算、重试和批改入口原因 |
| 修改 | `client/src/features/grading/GradingWorkspacePage.tsx` | 复核后刷新、自动定位、生成进度和报告操作 |
| 修改 | `backend/homework_judge/api/grading.py` | 最后复核后的幂等生成任务调度与失败响应 |
| 修改 | `backend/homework_judge/grading/review.py` | 最后复核状态转换边界与生成物失效 |
| 修改 | `backend/homework_judge/jobs/grading_pipeline.py` | 生成阶段恢复和失败边界（仅在测试暴露缺口时） |
| 修改 | `backend/homework_judge/artifacts/service.py` | 当前修订幂等与失败保持（仅在测试暴露缺口时） |
| 新建 | `tests/ui/action-feedback.test.tsx` | 统一反馈组件测试 |
| 新建 | `tests/ui/task-processing-actions.test.tsx` | 新建、删除和处理重试测试 |
| 新建 | `tests/ui/review-page-actions.test.tsx` | 题目复核页面按钮闭环测试 |
| 新建 | `tests/ui/student-submission-actions.test.tsx` | 学生答卷页面操作测试 |
| 新建 | `tests/ui/grading-review-report.test.tsx` | 批改复核到报告状态刷新测试 |
| 修改 | `backend/tests/unit/test_grading_review.py` | 复核状态与生成物失效测试 |
| 修改 | `backend/tests/unit/test_grading_pipeline.py` | 生成失败和恢复测试 |
| 修改 | `backend/tests/integration/test_grading_api.py` | 最后复核自动生成及报告下载测试 |

## T1：建立按钮审计基线

**文件：** `client/src/features/**`, `tests/ui/**`

**依赖：** 无

**步骤：**

1. 列出六个主要页面中所有按钮和按钮式链接。
2. 为每个操作记录导航、本地状态或服务端请求目标。
3. 标记缺少忙碌状态、错误捕获、成功刷新或禁用原因的操作。
4. 将清单映射到后续 T4-T10，避免遗漏和无关改动。

**验证：** 使用代码搜索核对每个 `<button>`、按钮式 `<Link>` 和生成物 `<a>` 均出现在审计清单中，且每项都有后续任务归属。

## T2：实现统一操作反馈组件

**文件：** `client/src/components/ActionFeedback.tsx`, `client/src/styles.css`

**依赖：** T1

**步骤：**

1. 实现成功/进度消息、错误消息和禁用原因的轻量组件。
2. 成功/进度使用 `role="status"`，错误使用 `role="alert"`。
3. 增加不只依赖颜色的文字和图标样式。
4. 保持组件无业务请求和全局状态依赖。

**验证：** 运行 `pnpm vitest run tests/ui/action-feedback.test.tsx`，确认三类消息和无内容状态均正确渲染。

## T3：对齐前端复核与生成物 API 契约

**文件：** `client/src/lib/api.ts`, `shared/contracts.ts`

**依赖：** 无

**步骤：**

1. 定义复核响应的 `reviewItemId`、`gradingRunId`、`questionResultId`、`status`、`score` 和 `remainingReasons`。
2. 让 `resolveGradingReview` 返回该明确类型。
3. 增加生成物可用性检查或受控打开辅助函数，解析 JSON 错误 envelope。
4. 保持 PDF 正常响应不经过 JSON schema 解析。

**验证：** 运行 `pnpm typecheck`，并用 API 客户端测试确认成功 PDF 与错误 envelope 都能被区分。

## T4：修复任务列表操作闭环

**文件：** `client/src/features/tasks/TaskListPage.tsx`

**依赖：** T1、T2

**步骤：**

1. 保留删除确认，补齐删除执行期间和失败后的就地反馈。
2. 确认删除按钮不会触发任务卡导航。
3. 删除成功后刷新列表并清除旧错误。
4. 为列表读取失败保留明确重试方式。

**验证：** 运行任务列表 UI 测试，模拟删除成功、失败、取消和卡片导航，观察每种结果都有可见反馈。

## T5：修复新建任务上传闭环

**文件：** `client/src/features/tasks/CreateTaskPage.tsx`

**依赖：** T1、T2

**步骤：**

1. 将上传入口改为语义完整的表单提交或明确按钮类型。
2. 缺少任一文件时显示具体原因并聚焦对应选择区。
3. 上传期间显示忙碌文字并阻止重复提交。
4. 上传失败后解除忙碌、保留已选文件并允许重试。

**验证：** 运行新建任务 UI 测试，覆盖缺文件、成功导航、服务端失败和重复点击。

## T6：修复识别进度重试闭环

**文件：** `client/src/features/processing/ProcessingPage.tsx`

**依赖：** T2

**步骤：**

1. 将裸异步重试改为带忙碌和错误状态的受控操作。
2. 重试成功后清除旧错误并刷新进度。
3. 重试失败后显示服务端错误并恢复按钮。
4. 避免在渲染阶段直接调度导航，改为副作用处理完成状态导航。

**验证：** 运行处理页 UI 测试，覆盖失败重试、请求失败、重复点击和完成后导航。

## T7：修复题目编辑器操作闭环

**文件：** `client/src/features/review/ReviewPage.tsx`

**依赖：** T2

**步骤：**

1. 为保存、确认和取消确认分别增加忙碌状态，防止同时或重复提交。
2. 为取消确认补齐异常捕获和错误显示。
3. 操作成功后等待相关查询刷新，再显示准确结果。
4. 切题时清除只属于上一题的临时消息。

**验证：** 运行题目复核 UI 测试，模拟三个操作的成功和失败，确认按钮状态、题目状态和提示同步变化。

## T8：修复复核导航、孤立答案和完成准入

**文件：** `client/src/features/review/ReviewPage.tsx`

**依赖：** T7

**步骤：**

1. 为“标记无关”增加忙碌、失败提示和重复点击保护。
2. 计算完成按钮的全部阻塞原因：未确认题目、孤立答案和请求进行中。
3. 在禁用按钮附近显示具体原因，条件满足后即时恢复可用。
4. 完成成功后显示任务已完成状态并刷新页面数据。

**验证：** 运行复核页 UI 测试，分别构造每种阻塞条件并确认提示；全部清理后完成请求只发送一次。

## T9：修复学生答卷页面操作闭环

**文件：** `client/src/features/students/StudentSubmissionsPage.tsx`

**依赖：** T2

**步骤：**

1. 上传前显示缺少文件原因，上传中防重复并在失败后恢复。
2. 补算全部题目区域和重新处理均显示忙碌、成功与失败。
3. 自动补算失败时保留可见错误，不因副作用静默吞掉。
4. 批改入口不可用时说明学生答卷或题框尚未就绪。

**验证：** 运行学生答卷 UI 测试，覆盖上传、补算、自动补算失败、重新处理和批改入口条件。

## T10：修复批改工作台复核刷新闭环

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`

**依赖：** T2、T3

**步骤：**

1. 复核提交前清除旧消息并防止重复提交。
2. 使用稳定查询前缀刷新运行、题目详情、题目列表、复核项和生成物。
3. 若响应仍有剩余复核项，自动选中下一道待复核题或显示剩余数量。
4. 若为最后一项，显示“正在生成批注和错题报告”，并持续轮询处理状态。
5. 请求失败时保留当前编辑内容并显示可重试错误。

**验证：** 运行批改工作台 UI 测试，覆盖非最后复核、最后复核、失败重试和重复点击。

## T11：修复批改工作台其他按钮与禁用原因

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`

**依赖：** T10

**步骤：**

1. 审计开始批改、继续处理、重新生成和错误位置调整的忙碌与失败反馈。
2. 为不可用的开始、调整位置和重试入口显示具体原因。
3. 筛选结果为空时显示明确空状态。
4. 确保分页和图层开关在处理中不会被遮挡或误触发请求。

**验证：** 运行工作台 UI 测试，覆盖各主按钮成功/失败、空筛选和禁用条件。

## T12：实现错题报告预览和下载错误反馈

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`, `client/src/lib/api.ts`

**依赖：** T3、T10

**步骤：**

1. 只选择 `current` 且修订号匹配的错题报告。
2. 点击预览或下载前验证生成物仍可用。
3. 验证成功后执行打开或下载；失败时留在当前页并显示服务端原因。
4. 生成阶段显示进度，完成后无整页刷新地出现报告入口。

**验证：** UI 测试分别返回有效 PDF、过期 409 和网络错误，确认入口及错误反馈正确。

## T13：验证并加固最后复核状态转换

**文件：** `backend/homework_judge/grading/review.py`, `backend/tests/unit/test_grading_review.py`

**依赖：** 无

**步骤：**

1. 增加多个复核项与最后复核项的状态转换测试。
2. 断言非最后项保持 `needs_review`，最后项原子地进入 `generating_annotation`。
3. 断言最终分数、结果修订和旧生成物失效正确。
4. 断言重复确认同一复核项返回冲突且不二次修改分数。
5. 仅在测试失败时修复状态转换实现。

**验证：** 运行 `pytest backend/tests/unit/test_grading_review.py`，新增和现有用例全部通过。

## T14：验证并加固生成任务调度幂等性

**文件：** `backend/homework_judge/api/grading.py`, `backend/tests/integration/test_grading_api.py`

**依赖：** T13

**步骤：**

1. 为最后一项复核接口增加任务启动断言。
2. 验证唯一任务键阻止同一运行重复生成。
3. 处理任务未能启动的边界，使接口或运行状态给出可见错误。
4. 保持复核事务已提交时的分数和审计记录不丢失。

**验证：** 运行最后复核集成测试，观察一次复核请求只启动一个生成任务并最终产生两类生成物。

## T15：验证报告生成、失败和只重试生成阶段

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`, `backend/homework_judge/artifacts/service.py`, `backend/tests/unit/test_grading_pipeline.py`

**依赖：** T14

**步骤：**

1. 注入批注成功、报告失败，断言运行进入可恢复失败。
2. 记录复核结果、最终分数和模型调用次数基线。
3. 调用重试并断言只执行生成物阶段，不重新评分。
4. 断言最终产生同一修订的当前批注和错题报告。
5. 仅在测试暴露缺口时修改流水线或生成物服务。

**验证：** 运行生成流水线单元测试，比较重试前后评分表、模型调用次数和生成物记录。

## T16：补齐最后复核到报告下载的 API 集成测试

**文件：** `backend/tests/integration/test_grading_api.py`

**依赖：** T14、T15

**步骤：**

1. 构造包含至少一个待复核错题的完整运行。
2. 提交最后复核并等待状态经过生成阶段到 `completed`。
3. 查询生成物并验证存在当前 `error_report`。
4. 验证预览和下载返回有效 PDF。
5. 重复请求和旧修订下载分别验证幂等与 409 拒绝。

**验证：** 运行目标集成测试，所有状态、生成物、PDF 和冲突断言通过。

## T17：补齐通用页面 UI 测试

**文件：** `tests/ui/action-feedback.test.tsx`, `tests/ui/task-processing-actions.test.tsx`

**依赖：** T2、T4、T5、T6

**步骤：**

1. 测试反馈组件的状态、警告和无障碍语义。
2. 测试任务删除、上传和处理重试的成功、失败与重复点击。
3. 验证禁用原因可见且条件满足后恢复。

**验证：** 运行两个目标测试文件，全部用例通过且无未处理 Promise 警告。

## T18：补齐题目复核与学生答卷 UI 测试

**文件：** `tests/ui/review-page-actions.test.tsx`, `tests/ui/student-submission-actions.test.tsx`

**依赖：** T7、T8、T9

**步骤：**

1. 测试保存、确认、取消确认、标记无关和完成准入。
2. 测试学生上传、补算、重新处理和批改入口条件。
3. 模拟服务端失败，确认按钮恢复并显示可读错误。

**验证：** 运行两个目标测试文件，全部操作的成功与失败断言通过。

## T19：补齐批改复核与报告 UI 测试

**文件：** `tests/ui/grading-review-report.test.tsx`

**依赖：** T10、T11、T12

**步骤：**

1. 模拟非最后复核项，验证剩余数量和下一题定位。
2. 模拟最后复核项，依次返回生成批注、生成报告和完成状态。
3. 验证报告入口在完成后出现且无需整页刷新。
4. 模拟生成失败、过期报告和下载失败，验证恢复入口和错误提示。

**验证：** 运行目标测试文件，状态序列、查询刷新和报告操作全部通过。

## T20：运行静态检查与完整自动化回归

**文件：** 全部修改文件

**依赖：** T13-T19

**步骤：**

1. 运行 TypeScript 类型检查。
2. 运行前端全部 UI 测试。
3. 运行后端单元与集成测试。
4. 运行 Ruff、mypy 和生产构建。
5. 修复本次变更引起的失败并重新执行对应检查。

**验证：** `pnpm typecheck`、`pnpm test:ui`、`pnpm test:python`、`pnpm lint` 和 `pnpm build` 均退出码为 0。

## T21：执行真实浏览器主流程验收

**文件：** 无实现文件；记录验证证据

**依赖：** T20

**步骤：**

1. 启动开发服务并检查首页无空白、错误遮罩或控制台错误。
2. 在 1280×720 下逐页点击审计清单中的主按钮。
3. 使用真实或隔离验收数据走完“批改工作台 → 全部复核 → 自动生成 → 预览/下载错题报告”。
4. 验证键盘可到达主要操作，固定栏和预览图层不遮挡按钮。
5. 关闭浏览器会话和开发服务，保留截图与实际结果摘要。

**验证：** Chrome/浏览器自动化流程显示报告 PDF 可预览和下载，页面无静默按钮、错误遮罩或控制台异常。

## 执行顺序

```text
T1 → T2 → T4 → T5 → T6 → T17
  └→ T7 → T8 → T9 → T18

T3 → T10 → T11 → T12 → T19

T13 → T14 → T15 → T16

T17 + T18 + T19 + T16 → T20 → T21
```

前端页面任务和后端状态机任务可分别推进，但在完整回归前必须全部完成。
