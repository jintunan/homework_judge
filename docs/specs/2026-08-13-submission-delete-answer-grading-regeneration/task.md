# 学生答卷删除与当前题答案/批改设置重生成 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `backend/homework_judge/submissions/deletion.py` | 答卷删除范围、任务取消与文件/数据库编排 |
| 修改 | `backend/homework_judge/files/storage.py` | 答卷文件及报告目录的安全暂存、回滚与清理 |
| 修改 | `backend/homework_judge/api/submissions.py` | 单份学生答卷删除接口 |
| 新建 | `backend/homework_judge/review/question_images.py` | 当前题题框裁剪和参考答案页图像准备 |
| 新建 | `backend/homework_judge/grading/answer_grading_generation.py` | 模型提示词、响应模型、规范化与题型校验 |
| 新建 | `backend/homework_judge/review/answer_grading_drafts.py` | 草稿捕获、运行、预览和原子应用 |
| 新建 | `backend/homework_judge/api/answer_grading_drafts.py` | 草稿生成和应用 HTTP 接口 |
| 修改 | `backend/homework_judge/api/router.py` | 注册草稿接口 |
| 修改 | `backend/homework_judge/review/invalidation.py` | 答案/评分上下文变更的全卷修订失效帮助函数 |
| 修改 | `backend/homework_judge/grading/blank_initialization.py` | 复用确定性均分与三空信号处理 |
| 修改 | `backend/homework_judge/schemas.py` | 草稿结构与响应校验类型 |
| 修改 | `shared/contracts.ts` | 删除结果、草稿预览和应用结果契约 |
| 修改 | `client/src/lib/api.ts` | 删除、生成草稿和应用草稿客户端方法 |
| 新建 | `client/src/components/ConfirmDeleteSubmissionDialog.tsx` | 单份答卷永久删除确认框 |
| 修改 | `client/src/features/students/StudentSubmissionsPage.tsx` | 删除入口、mutation、选择与缓存处理 |
| 新建 | `client/src/features/grading/blank-score-allocation.ts` | 前端 Decimal 字符串均分与合计校验 |
| 新建 | `client/src/features/grading/AnswerGradingDraftDialog.tsx` | 当前值/草稿值题型化对比与应用 |
| 修改 | `client/src/features/grading/GradingConfigPanel.tsx` | 重生成入口、增删空均分、行内验证、刷新 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 草稿应用后的复核数据刷新与全局反馈 |
| 修改 | `client/src/styles.css` | 列表删除、确认框、对比预览和校验样式 |
| 新建 | `backend/tests/unit/test_submission_deletion.py` | 删除范围、任务键和文件暂存回滚单测 |
| 新建 | `backend/tests/unit/test_answer_grading_generation.py` | 四题型解析、三空和无效输出单测 |
| 修改 | `backend/tests/unit/test_blank_initialization.py` | 三空均分和空位信号回归（若现有文件名不同则落在对应单测） |
| 修改 | `backend/tests/integration/test_student_submission_api.py` | 删除 API、级联、文件和其他答卷隔离 |
| 新建 | `backend/tests/integration/test_answer_grading_draft_api.py` | 生成、预览、应用、冲突、失效和审计集成测试 |
| 新建 | `tests/ui/student-submission-delete.test.tsx` | 删除确认、成功/失败和选择迁移 UI 测试 |
| 新建 | `tests/ui/answer-grading-regeneration.test.tsx` | 支持题型、预览、取消、应用和错误 UI 测试 |
| 修改 | `tests/ui/grading-config.test.tsx` | 增删空自动均分、可修改和行内校验测试 |

## T1：建立共享逐空分值分配规则

**文件：** `backend/homework_judge/grading/blank_initialization.py`、
`client/src/features/grading/blank-score-allocation.ts`

**依赖：** 无

**步骤：**

1. 固化“总分按最小 0.01 单位分配、前 n-1 空取基准、最后一空取剩余”的后端函数行为。
2. 在前端用十进制字符串/整数分值单位实现同一算法，避免 JavaScript 浮点误差。
3. 提供重排空位键、顺序、分值以及计算逐空合计/差额的纯函数。
4. 覆盖 5÷3、5÷2、小于空位数的正小数总分、无效总分和零空位边界。

**验证：** 运行目标后端单测和前端纯函数测试，确认 5 分三空严格得到
`1.67, 1.67, 1.66` 且字符串求和为 `5.00`。

## T2：完善填空设置增删与表单校验

**文件：** `client/src/features/grading/GradingConfigPanel.tsx`、
`tests/ui/grading-config.test.tsx`

**依赖：** T1

**步骤：**

1. 将“增加一空”和删除空统一改为重排 B1…Bn 并调用共享均分函数。
2. 保证自动均分只发生在增删动作；教师手工修改某空分值后不被其他普通输入覆盖。
3. 提交前检查标准答案、正分值、连续键和逐空合计，显示每行问题和总分差额。
4. 保留服务端错误详情并转换为中文可操作反馈。
5. 增加两空到三空、三空删回两空、教师覆盖分值、缺答案和合计不等的 UI 测试。

**验证：** 运行 `grading-config` UI 测试；观察 5 分三空可填写第三空答案并成功发出总和为
5.00 的保存请求。

## T3：实现答卷删除文件暂存与回滚工具

**文件：** `backend/homework_judge/files/storage.py`、
`backend/tests/unit/test_submission_deletion.py`

**依赖：** 无

**步骤：**

1. 根据服务器已知的任务 ID、答卷 ID和批改运行 ID构造上传、页面、报告精确路径。
2. 对每个源路径和暂存路径执行解析后父目录校验，拒绝空 ID、路径分隔符、根目录或越界路径。
3. 实现同数据目录删除暂存区的移动、失败回滚、提交后清理，并让错误可由上层区分。
4. 测试正常暂存/清理、不存在目录幂等、越界拒绝、中途移动失败回滚和清理范围隔离。

**验证：** 运行删除服务单测；临时数据目录中只有目标答卷和目标运行目录被移动/清除。

## T4：实现答卷删除用例和 API

**文件：** `backend/homework_judge/submissions/deletion.py`、
`backend/homework_judge/api/submissions.py`、
`backend/tests/integration/test_student_submission_api.py`

**依赖：** T3

**步骤：**

1. 查询答卷归属、所有处理修订和批改运行；不存在时返回稳定 404。
2. 枚举学生主任务、新流程任务、修订任务、批改任务和报告任务键，调用 `JobManager.cancel` 等待清理。
3. 暂存目标文件，在事务中写 `student_submission_deleted` 任务审计并删除答卷主记录。
4. 数据库异常时恢复暂存文件；成功时提交清理并返回删除结果与取消数。
5. 集成测试完整级联、文件清理、活动任务取消、其他答卷/模板保留、404 和文件失败可重试。

**验证：** 运行学生答卷 API 集成测试；删除后目标详情 404、派生表计数为 0、目标目录不存在，
同任务另一答卷详情和文件仍正常。

## T5：接入答卷列表删除交互

**文件：** `shared/contracts.ts`、`client/src/lib/api.ts`、
`client/src/components/ConfirmDeleteSubmissionDialog.tsx`、
`client/src/features/students/StudentSubmissionsPage.tsx`、`client/src/styles.css`、
`tests/ui/student-submission-delete.test.tsx`

**依赖：** T4

**步骤：**

1. 增加删除响应契约和客户端方法。
2. 把整块列表按钮重构为行容器、选择按钮和独立删除按钮，保持现有 active 与状态样式。
3. 实现显示学生身份、永久删除范围、确认/取消/忙碌/错误状态的对话框。
4. 成功后移除旧详情缓存、刷新列表、复位历史和页码，并按删除前顺序选择下一项或前一项。
5. 测试取消不请求、确认参数正确、删除失败保留状态、删除当前/非当前项以及删除最后一项。

**验证：** 运行新增 UI 测试；无嵌套 button 警告，键盘和无障碍名称可以区分选择与删除。

## T6：抽取当前题图像与参考答案输入准备

**文件：** `backend/homework_judge/review/question_images.py`、
`backend/homework_judge/review/question_rerecognition.py`、相关单题识别测试

**依赖：** 无

**步骤：**

1. 从现有单题重识别提取模板页读取、尺寸核对、归一化题框裁剪和有序片段结构。
2. 新帮助模块支持读取当前题当前题框项的全部裁剪，并保留现有错误码/几何行为。
3. 增加按答案条目来源页读取参考答案页面的安全方法，只允许同任务 `answer` 文档页面。
4. 让单题重识别复用新模块，确保行为和既有测试不变。

**验证：** 运行单题重识别单元与题框 API 集成测试；跨页裁剪顺序、尺寸错误和路径安全测试继续通过。

## T7：定义四题型草稿模型契约与严格校验

**文件：** `backend/homework_judge/grading/answer_grading_generation.py`、
`backend/homework_judge/schemas.py`、
`backend/tests/unit/test_answer_grading_generation.py`

**依赖：** T1

**步骤：**

1. 编写专用系统提示词和版本，明确只处理当前题、参考题图、列全可见作答位置和输出 JSON。
2. 定义公共草稿及 choice/fill/calculation 题型专用 Pydantic 响应模型。
3. 规范化选择答案、逐空键/顺序/类型/同义答案和计算评分点。
4. 复用逐空均分与现有计算评分政策校验总分、`FINAL_ANSWER` 和依赖关系。
5. 为示例电现象题构造视觉模型模拟响应，断言生成三个空和 `1.67/1.67/1.66`。
6. 覆盖无效 JSON、少空警告、选项越界、空答案、题型不符、分值不符和评分点违规。

**验证：** 运行生成模块单测；所有不合规模型输出在写入运行预览前被拒绝并返回稳定错误。

## T8：实现草稿捕获和生成运行

**文件：** `backend/homework_judge/review/answer_grading_drafts.py`、
`backend/homework_judge/review/question_images.py`、
`backend/tests/integration/test_answer_grading_draft_api.py`

**依赖：** T6、T7

**步骤：**

1. 查询有效题目、匹配、答案条目、题框项、正式评分配置和当前冻结细则，拒绝不支持/重复题。
2. 计算并保存题目、匹配、题框、逐空配置和评分细则的版本/哈希捕获。
3. 创建 `answer_grading_regeneration` 运行，准备题框裁剪和答案页图文输入，调用模型。
4. 保存原始响应、用量、规范化草稿和当前内容快照，阶段设为 `preview_ready`。
5. 失败时记录错误和可能取得的原始响应，确认正式答案、配置和学生结果没有变化。

**验证：** 运行草稿 API 集成测试的生成部分；四类题返回题型化对比，运行记录包含输入来源和模型证据。

## T9：实现通用答案/评分上下文失效

**文件：** `backend/homework_judge/review/invalidation.py`、相关失效单元/集成测试

**依赖：** 无

**步骤：**

1. 新增按任务解除所有当前学生处理修订的帮助函数，保留全部版本行和识别证据。
2. 清空答卷当前处理指针，把答卷与题框区域状态置为待重新处理并写明确原因。
3. 将所有旧批改运行设为 `is_stale=1`、可重试，将当前/生成中产物设为 stale。
4. 复用该函数于草稿应用，并保持既有题框/逐空配置失效函数兼容。
5. 测试多个学生、多个历史修订、无学生数据和已有 stale 数据的幂等行为。

**验证：** 运行目标测试；旧行数量不减少，所有 current 指针解除且旧报告不再标为 current。

## T10：实现草稿原子应用

**文件：** `backend/homework_judge/review/answer_grading_drafts.py`、
`backend/homework_judge/review/invalidation.py`、
`backend/tests/integration/test_answer_grading_draft_api.py`

**依赖：** T8、T9

**步骤：**

1. 按 run ID 加载服务器草稿，校验题目归属、`preview_ready` 和未应用状态。
2. 检查活动学生/批改任务，并重算捕获中的所有当前版本与哈希。
3. 在事务中保留答案条目来源，写教师答案/解析；按题型写普通配置、新逐空版本或新冻结评分细则。
4. 保留原题确认状态，调用通用失效，更新运行阶段并写教师审计。
5. 测试正常应用、只影响当前题、重复应用、各种版本冲突、活动任务阻断、事务回滚和历史保留。

**验证：** 运行草稿 API 集成测试；失败场景前后数据库业务快照相同，成功场景旧学生结果均 stale 且可重处理。

## T11：暴露草稿 API 与共享契约

**文件：** `backend/homework_judge/api/answer_grading_drafts.py`、
`backend/homework_judge/api/router.py`、`backend/homework_judge/schemas.py`、
`shared/contracts.ts`、`client/src/lib/api.ts`

**依赖：** T8、T10

**步骤：**

1. 添加生成与应用路由，使用现有数据库、设置、模型客户端依赖和统一响应封装。
2. 将后端草稿比较、题型专用内容、应用结果与错误详情映射到共享 TypeScript 契约。
3. 添加前端生成/应用方法，生成接口不接受文件路径或可篡改草稿内容。
4. 验证重复题和不支持题型即使绕过界面也获得稳定 409。

**验证：** 后端路由测试和 TypeScript 类型检查通过，API 返回字段与共享契约一致。

## T12：实现草稿对比预览与复核页接入

**文件：** `client/src/features/grading/AnswerGradingDraftDialog.tsx`、
`client/src/features/grading/GradingConfigPanel.tsx`、
`client/src/features/review/ReviewPage.tsx`、`client/src/styles.css`、
`tests/ui/answer-grading-regeneration.test.tsx`

**依赖：** T11

**步骤：**

1. 仅为四类非重复题显示入口，处理生成忙碌、失败和再次生成。
2. 对话框以公共字段加题型专用表格展示当前值、草稿值、差异和空位信号警告。
3. 取消只关闭；应用只提交 run ID，处理成功、版本冲突和活动任务阻断。
4. 应用成功刷新 review、grading-config、rubric-versions 和学生上传门禁相关查询，提示需要重处理学生答卷。
5. 测试四类显示、三类隐藏、生成前后正式内容不变、取消、应用、冲突、模型错误和三空预览。

**验证：** 运行新增 UI 测试；预览可由键盘操作，焦点进入/退出正确，错误不会关闭对话框或丢失当前内容。

## T13：执行集成回归与验收

**文件：** 本任务涉及的所有测试与文档

**依赖：** T2、T5、T10、T12

**步骤：**

1. 运行删除、填空初始化、草稿生成/应用和 UI 的目标测试，修复所有失败。
2. 运行完整前端测试、后端测试、类型检查、Ruff、Mypy 和生产构建。
3. 按 `checklist.md` 使用临时数据库和数据目录逐项记录证据。
4. 检查工作区差异，只包含本功能文件和必要的兼容调整，不覆盖现有无关修改。

**验证：** `pnpm run lint`、`pnpm test`、`pnpm build` 全部通过，checklist 每项有实际结果。

## 执行顺序

```text
T1 → T2
 │
 └──→ T7 ──→ T8 ──→ T10 ──→ T11 ──→ T12 ──┐
             ↑       ↑                      │
T6 ──────────┘       │                      │
                     │                      ├──→ T13
T9 ──────────────────┘                      │
                                            │
T3 → T4 → T5 ───────────────────────────────┘
```

T1/T3/T6/T9 可独立执行；涉及同一文件时仍按任务顺序合并，避免覆盖并行修改。
