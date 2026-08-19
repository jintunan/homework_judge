# 题框驱动的逐空识别与模型批改 Tasks

> 本清单在四份文档获得用户明确批准后才能执行。每个任务只做一个聚焦改动；先写或调整测试，再改实现，验证通过后才进入依赖它的任务。

## 执行状态（2026-08-10）

| 范围 | 状态 | 证据/说明 |
| --- | --- | --- |
| T1—T52 | 代码与自动化完成 | schema v8、题框/配置/处理/评分版本、门禁、页面校正、逐空识别/判分、历史恢复和三层 UI 已纳入全量测试 |
| T53—T54 | 自动化完成，人工 oracle 待验收 | 通用矩阵及 q8/q11 候选回归通过；两个真实样本仍为 `reviewStatus=candidate`，没有伪称教师金标 |
| T55—T57 | 完成 | 生产特判守卫、冲突旧规则修订、450 项 Python、68 项 UI、类型/静态/构建/迁移/diff 门禁通过 |
| T58 | 完成 | 修复单题保存的全局几何死锁、待确认循环阻断和三栏拥挤；增加零模型成本的单栏题框补齐，并完成真实任务 API/Edge 验证 |
| T59 | 完成 | 解除不可配置的“独立锚点”保存死锁；实际第 9 题只剩 `blank_score_missing` blocker，450 项 Python、68 项 UI、类型/静态/构建通过，真实配置验证模型调用为 0 |
| T60 | 完成 | 消除 `blank_score_missing` 人工门禁和题框层错误归因；安全配置在题目确认/任务完成/学生处理/批改入口自动确认，实际第 9 题生成 v1（1.33/1.33/1.34），模型调用 0；452 项 Python、68 项 UI、类型/静态/构建通过 |
| 人工验收 | 未执行 | 仍需教师在真实 Chrome/Edge 与原始页完成四档缩放、题框和交互走查 |

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `backend/homework_judge/question_frames/__init__.py` | 题框子系统包 |
| 新建 | `backend/homework_judge/question_frames/service.py` | 题框集生成、分叉、编辑、确认、冻结和门禁 |
| 新建 | `backend/homework_judge/question_frames/validation.py` | 题框片段与跨题几何校验 |
| 新建 | `backend/homework_judge/api/question_frames.py` | 题框集 API |
| 新建 | `backend/homework_judge/recognition/blank_detection.py` | 已确认题框内的空位检测与键生成 |
| 新建 | `client/src/features/review/TemplateQuestionFrameEditor.tsx` | 模板原页题框编辑器 |
| 新建 | `client/src/features/review/question-frame-geometry.ts` | 编辑器纯几何函数 |
| 新建 | `client/src/features/students/StudentAlignmentEditor.tsx` | 学生页面级配准校正 |
| 修改 | `backend/homework_judge/db/database.py` | schema v8 与 v7 历史迁移 |
| 修改 | `backend/homework_judge/schemas.py` | 题框、空位、配准和逐空 API 契约 |
| 修改 | `backend/homework_judge/api/review.py` | 复核数据、题目确认和门禁 |
| 修改 | `backend/homework_judge/api/rubrics.py` | 不可变空位配置版本 |
| 修改 | `backend/homework_judge/api/submissions.py` | 上传门禁、处理代次和配准校正 |
| 修改 | `backend/homework_judge/api/grading.py` | 逐空识别、判定和复核详情 |
| 修改 | `backend/homework_judge/api/router.py` | 注册题框路由 |
| 修改 | `backend/homework_judge/jobs/pipeline.py` | 初始模型题框写入草稿集 |
| 修改 | `backend/homework_judge/jobs/question_region_pipeline.py` | 移除学生侧自动补框用途 |
| 修改 | `backend/homework_judge/jobs/student_pipeline.py` | 映射硬门禁、处理代次和逐空识别 |
| 修改 | `backend/homework_judge/jobs/grading_pipeline.py` | 删除 segment 下标绑定，按 `blankKey` 组装 |
| 修改 | `backend/homework_judge/recognition/normalizer.py` | 停止答案框扩张完整题框 |
| 修改 | `backend/homework_judge/recognition/prompts.py` | 完整题框、空位和键控识别提示 |
| 修改 | `backend/homework_judge/recognition/parser.py` | 严格键集合解析 |
| 修改 | `backend/homework_judge/recognition/service.py` | 分阶段模型入口和有限重试 |
| 修改 | `backend/homework_judge/alignment/geometry.py` | 控制点、面积、裁切和相交计算 |
| 修改 | `backend/homework_judge/alignment/regions.py` | 固定 frame set 的批量映射和质量校验 |
| 修改 | `backend/homework_judge/alignment/models.py` | 配准修订与质量指标 |
| 修改 | `backend/homework_judge/grading/blank_initialization.py` | 三方一致性，不复制复合框、不均分 |
| 修改 | `backend/homework_judge/grading/blank_config_confirmation.py` | 严格 blocker 与版本确认 |
| 修改 | `backend/homework_judge/grading/contracts.py` | 强类型逐空输入与版本引用 |
| 修改 | `backend/homework_judge/grading/prompts.py` | 每空模型判分输出契约 |
| 修改 | `backend/homework_judge/grading/fill.py` | 每空模型判定、工具证据与冲突处理 |
| 修改 | `backend/homework_judge/grading/calculation.py` | 后端 Decimal 汇总 |
| 修改 | `backend/homework_judge/grading/review.py` | 按键修正识别和判定 |
| 修改 | `backend/homework_judge/review/invalidation.py` | frame set/config 到下游的失效传播 |
| 修改 | `backend/homework_judge/review/lifecycle.py` | 有效题集合变化与 frame set 分叉 |
| 修改 | `backend/homework_judge/config.py` | 映射阈值 |
| 修改 | `shared/contracts.ts`、`shared/schemas.ts` | 前后端共享契约与运行时校验 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 题框/空位复核工作区 |
| 修改 | `client/src/features/grading/GradingConfigPanel.tsx` | 逐空答案、锚点、分值和 blocker |
| 修改 | `client/src/features/grading/GradingPageOverlay.tsx` | 三层叠加显示 |
| 修改 | `client/src/features/grading/GradingWorkspacePage.tsx` | 逐空复核与版本状态 |
| 修改 | `client/src/features/students/StudentPageOverlay.tsx` | 原样绘制映射多边形 |
| 修改 | `client/src/features/students/StudentSubmissionsPage.tsx` | 删除自动补框/扩框并接入校正 |
| 修改 | `client/src/lib/api.ts`、`client/src/styles.css` | API 调用与图例/编辑器样式 |
| 新建/修改 | `backend/tests/**`、`tests/ui/**` | 参数化通用场景、单元、集成、UI 和真实样本回归 |
| 修改 | `docs/acceptance-report.md`、旧 spec 说明 | 标注被本设计取代的旧结论 |

## 执行约定

- Python 局部验证统一使用 `.\.venv\Scripts\python.exe -m pytest <path> -q -p no:cacheprovider`。
- UI 局部验证使用 `pnpm exec vitest run <test-file>`。
- 数据库迁移任务必须在 `PRAGMA foreign_keys=ON` 下运行，并在提交前执行 `PRAGMA foreign_key_check`。
- 所有模型测试使用固定响应或 spy，不依赖真实网络模型。
- 任一任务发现旧数据会被删除、旧作业可覆盖新版本或识别请求包含标准答案，立即停止后续任务并修正。

## 第一阶段：验收基线与数据版本

### T1：建立通用参数化基线与第 8/11 题 oracle

**文件：** 新建 `backend/tests/fixtures/generic_blank_layout_cases.json`、`backend/tests/fixtures/q8_full_frame_oracle.json`、`backend/tests/fixtures/q11_three_blanks_oracle.json`

**依赖：** 无

**步骤：**

1. 先建立与具体学科无关的 1/2/3/5 空参数化案例，覆盖同一行、跨行、多个小问、共享视觉上下文、选项干扰以及变化后的题号/文本/页码/坐标。
2. 每个题框 oracle 都使用通用 `requiredContentSentinels`（题干、小问、图表、声明的选项/答题区）和 `forbiddenSentinels`（前后题、页眉页脚、邻近分区），同时验证“完整但不过度扩张”。再为第 8/11 题记录相同结构的真实样本数据、SHA、锚点、答案和分值。
3. 真实 oracle 加入 `reviewedBy`、`reviewedAt`；未由教师人工确认前只能标记 `candidate`。所有 fixture 仅供测试读取，生产代码不得导入。

**验证：** 运行 fixture schema 测试，期望通用案例覆盖 1/2/3/5 空且改变题号/文本后不变；两个真实 JSON 可解析、SHA 匹配，第 11 题键集合恰为 B1/B2/B3。

### T2：为 schema v8 写空库迁移测试

**文件：** `backend/tests/unit/test_database.py`

**依赖：** 无

**步骤：**

1. 增加空数据库初始化到 v8 的测试。
2. 断言题框集、题框项、题框片段、空位配置版本、处理修订、页面配准修订和逐空响应表/索引存在。
3. 断言所有 current 指针和唯一约束存在。

**验证：** `.\.venv\Scripts\python.exe -m pytest backend/tests/unit/test_database.py -q -p no:cacheprovider`；测试应先因缺 schema 失败。

### T3：创建题框集与空位配置版本 schema

**文件：** `backend/homework_judge/db/database.py`

**依赖：** T2

**步骤：**

1. 添加 `question_frame_sets/items/regions` 及必要索引、外键和状态约束。
2. 给 `tasks` 增加当前题框集指针。
3. 添加 `question_blank_config_versions/definition_versions`，给兼容配置表增加当前版本指针。

**验证：** 重跑 T2，期望新表/索引断言通过且现有数据库单元测试无回归。

### T4：为学生处理代次写迁移保留测试

**文件：** `backend/tests/unit/test_database.py`、`backend/tests/unit/test_grading_database.py`

**依赖：** T2

**步骤：**

1. 构造含学生原页、配准、映射题框、响应、证据和评分外键的 v7 数据库。
2. 断言升级后旧 ID/外键仍可查，legacy processing revision 为当前历史代。
3. 断言可以插入第二代映射/响应而不违反旧唯一键，且两代并存。

**验证：** 运行两个测试文件；新增测试应先因缺处理代次失败。

### T5：实现学生处理与配准修订 schema

**文件：** `backend/homework_judge/db/database.py`

**依赖：** T3、T4

**步骤：**

1. 添加 `student_processing_revisions`、`student_page_alignment_revisions` 和提交 current 指针。
2. 用建表/复制/校验/换名安全重建 `student_question_regions` 与 `student_responses`，把唯一键改为处理代次范围；保留原 ID。
3. 添加 `student_blank_responses` 及版本/证据字段；不得给旧 CHECK 写入不允许的 stale 状态。

**验证：** 重跑 T4；断言 `PRAGMA foreign_key_check` 返回空集合，旧代与新代均可查询。

### T6：迁移 legacy 题框和填空配置

**文件：** `backend/homework_judge/db/database.py`、`backend/tests/unit/test_database.py`

**依赖：** T3、T5

**步骤：**

1. 把旧 `question_regions_json` 转为任务 version 1 draft，所有有效题项为 pending。
2. 把旧空位配置转为 legacy pending 版本；缺页码、复合框、区域冲突和自动均分均记录 blocker。
3. 保留旧 JSON 和旧审计，不自动确认任何历史数据。

**验证：** 新增 `test_v8_migration_preserves_old_processing_generations_and_marks_them_stale` 并运行数据库测试，期望模型置信度 1.0 的旧题框仍为待确认。

### T7：定义共享版本与状态契约

**文件：** `backend/homework_judge/schemas.py`、`shared/contracts.ts`、`shared/schemas.ts`

**依赖：** T3、T5

**步骤：**

1. 定义 frame set/item/fragment、blank config version、processing revision、alignment revision 和分层错误契约。
2. 空位锚点为可选字段；提供时必须包含模板页 ID、页码、坐标空间和归一化 box，未提供时由完整题框承担共享识别范围。
3. 为请求增加 expected revision/version，响应增加 current/stale 派生状态。

**验证：** `pnpm run typecheck` 与 `.\.venv\Scripts\python.exe -m mypy backend/homework_judge`，期望契约编译通过。

## 第二阶段：模板完整题框

### T8：为题框片段几何校验写失败测试

**文件：** 新建 `backend/tests/unit/test_question_frame_validation.py`

**依赖：** T7

**步骤：**

1. 覆盖非法页码、NaN/Infinity、零/负面积、越界、重复 regionKey 和片段顺序冲突。
2. 覆盖同页跨题严重重叠、允许的小容差和跨页题片段。
3. 断言错误包含稳定 code、题号和 regionKey。

**验证：** 运行新测试，期望先因缺实现失败。

### T9：实现题框几何校验

**文件：** 新建 `backend/homework_judge/question_frames/validation.py`、`backend/homework_judge/question_frames/__init__.py`

**依赖：** T8

**步骤：**

1. 实现单片段有限值、页归属、范围和正面积校验。
2. 实现题内唯一/排序和题间相交比例校验。
3. 返回结构化 blocker，不使用模型置信度决定通过。

**验证：** 重跑 T8，期望全部通过；再运行现有 normalizer/geometry 测试确认无回归。

### T10：为题框集分叉与乐观锁写测试

**文件：** 新建 `backend/tests/unit/test_question_frame_versions.py`

**依赖：** T3、T9

**步骤：**

1. 覆盖初始 v1 draft、编辑后题项 pending、逐题确认和整套冻结。
2. 覆盖冻结版本不可更新；编辑冻结版本产生 v2 draft并携带未改题确认。
3. 用同一 expectedRevision 并发保存两次，断言只有一次成功。

**验证：** 运行新测试，期望先因缺 service 失败。

### T11：实现题框集服务

**文件：** 新建 `backend/homework_judge/question_frames/service.py`

**依赖：** T9、T10

**步骤：**

1. 实现模型/迁移草稿创建、草稿项替换和审计记录。
2. 实现冻结集编辑时分叉、未改项携带和被改项重开。
3. 实现逐题确认、整套冻结、current 指针及 expected revision 原子检查。

**验证：** 重跑 T10，期望状态机、不可变性和并发测试全部通过。

### T12：为题框 API 与统一门禁写集成测试

**文件：** 新建 `backend/tests/integration/test_question_frame_api.py`、修改 `backend/tests/integration/test_student_submission_api.py`

**依赖：** T11

**步骤：**

1. 覆盖查询、保存、重开、逐题确认、整套冻结和 409 revision 冲突。
2. 覆盖缺一题、部分确认、全部确认三种上传/重试结果；重复或排除题不计入 gate。
3. 断言拒绝发生在上传文件落盘之前，错误含当前版本和未确认题号。

**验证：** 运行两个集成测试文件，新增场景应先因缺路由/gate 失败。

### T13：实现题框 API 和复核响应

**文件：** 新建 `backend/homework_judge/api/question_frames.py`；修改 `backend/homework_judge/api/router.py`、`backend/homework_judge/api/review.py`、`backend/homework_judge/api/submissions.py`

**依赖：** T7、T11、T12

**步骤：**

1. 接入题框集查询、生成、题项 PATCH、确认/重开和整套冻结路由。
2. Review 响应返回模板页尺寸、题框集、逐题状态和服务端 `studentUploadGate`。
3. 上传、处理重试、完成任务和题目确认统一调用同一个 gate 服务。

**验证：** 重跑 T12，期望所有 API 状态与错误契约通过。

### T14：把初始题框模型结果写入草稿集

**文件：** `backend/homework_judge/jobs/pipeline.py`、`backend/homework_judge/recognition/prompts.py`、`backend/homework_judge/recognition/parser.py`、`backend/homework_judge/recognition/normalizer.py`

**依赖：** T11

**步骤：**

1. 初始识别用题号/结构返回完整题框候选，并写入当前 draft frame set。
2. 保留模型置信度和 issues，但不改变题项 pending 状态。
3. 停止 `complete_question_regions()` 用答案框扩张题框；相邻题边界只产生草稿建议或 blocker。

**验证：** 运行 `backend/tests/unit/test_parser.py`、`test_boundary_reconciliation.py`、`test_consolidator.py`，并新增断言“模型结果不会自动 confirmed”。

### T15：为题框编辑器纯几何写测试

**文件：** 新建 `tests/ui/question-frame-geometry.test.ts`

**依赖：** T7

**步骤：**

1. 覆盖像素/归一化往返、浏览器缩放、拖动边界和八向缩放。
2. 覆盖重画、跨页片段键和小于最小尺寸的拒绝。
3. 使用不同自然图像尺寸、题号和矩形坐标验证结果稳定。

**验证：** `pnpm exec vitest run tests/ui/question-frame-geometry.test.ts`，期望先因缺实现失败。

### T16：实现编辑器纯几何层

**文件：** 新建 `client/src/features/review/question-frame-geometry.ts`

**依赖：** T15

**步骤：**

1. 实现坐标变换、clamp、drag、resize 和新矩形生成纯函数。
2. 保持 regionKey/页码，拒绝非有限值和非正面积。
3. 不在该层发请求或读取 DOM 全局状态。

**验证：** 重跑 T15，期望全部通过；运行 `pnpm run typecheck`。

### T17：实现模板题框编辑器基础显示

**文件：** 新建 `client/src/features/review/TemplateQuestionFrameEditor.tsx`、修改 `client/src/styles.css`、新建 `tests/ui/question-frame-review.test.tsx`

**依赖：** T7、T16

**步骤：**

1. 用图片自然尺寸 `viewBox` 绘制当前题和其他题的片段。
2. 加入 draft/confirmed/error 样式、模型建议标识、固定图例和页码切换。
3. 用任意题号的单页/跨页多片段测试选中态和“题目确认”与“题框确认”两个不同标签；第 11 题只作为额外截图回归。

**验证：** `pnpm exec vitest run tests/ui/question-frame-review.test.tsx`。

### T18：实现题框编辑交互与保存冲突

**文件：** `client/src/features/review/TemplateQuestionFrameEditor.tsx`、`client/src/lib/api.ts`、`tests/ui/question-frame-review.test.tsx`

**依赖：** T13、T16、T17

**步骤：**

1. 接入拖动、缩放、重画、片段增删和撤销未保存修改。
2. 保存携带 expectedRevision；脏数据或几何 blocker 存在时禁止确认。
3. 409 冲突时保留本地草稿并提示刷新，不自动覆盖服务端。

**验证：** 重跑 UI 测试，期望编辑动作、请求内容、脏状态和冲突提示通过。

### T19：接入 ReviewPage 逐题确认和任务 gate

**文件：** `client/src/features/review/ReviewPage.tsx`、`client/src/lib/api.ts`、`client/src/styles.css`、`tests/ui/question-frame-review.test.tsx`

**依赖：** T13、T18

**步骤：**

1. 布局题目列表、模板原页和属性栏，显示 frame set 版本及已确认 X/Y。
2. 加入上一/下一未确认题、逐题确认、整套冻结和 blocker 导航。
3. 学生入口/上传控件只使用服务端 gate；题框未冻结时显示具体题号。

**验证：** 重跑 UI 测试并运行 `pnpm run typecheck`。

### T20：实现题框变化的失效传播

**文件：** `backend/homework_judge/review/invalidation.py`、`backend/homework_judge/review/lifecycle.py`、`backend/tests/unit/test_question_lifecycle.py`、`backend/tests/unit/test_question_frame_versions.py`

**依赖：** T11、T13

**步骤：**

1. frame set 分叉时把依赖旧集的当前空位配置、处理修订、评分运行和产物标为非当前/过期。
2. 题型、影响空位结构的题干或有效题集合变化时分叉 frame set 或使配置 stale。
3. 后台作业提交前比较捕获的 frameSetId/current 指针，旧作业只能记录 abandoned 事件。

**验证：** 运行两个单元测试，断言旧快照仍可查且迟到作业不能覆盖新版本。

## 第三阶段：学生页面配准与题框映射

### T21：锁定“学生侧不再检测题框”的失败测试

**文件：** `backend/tests/unit/test_student_pipeline.py`、`backend/tests/unit/test_student_recognition.py`

**依赖：** T13

**步骤：**

1. 给 `recognize_question_regions()` 放置会抛错的 spy。
2. 分别运行正常处理、缺题框、历史提交重试，断言学生流程调用次数恒为 0。
3. 缺确认 frame set 时断言在渲染/识别前返回门禁错误。

**验证：** 运行两个测试文件，新增断言应先因 `_ensure_template_regions()` 失败。

### T22：移除学生侧补框和题框启发式改写

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、`backend/homework_judge/jobs/question_region_pipeline.py`、`backend/homework_judge/recognition/normalizer.py`

**依赖：** T21

**步骤：**

1. 删除学生流水线调用题框模型和“数组非空即复用”的补框分支。
2. `question_region_pipeline` 只允许生成模板草稿/迁移候选，不自动映射或激活。
3. 学生映射只从捕获的 confirmed frameSetId 读取片段。

**验证：** 重跑 T21，期望 spy 零调用且门禁错误稳定。

### T23：为映射质量失败关闭写测试

**文件：** `backend/tests/unit/test_alignment_geometry.py`、`backend/tests/unit/test_alignment_regions.py`、`backend/tests/unit/test_student_pipeline.py`

**依赖：** T9、T22

**步骤：**

1. 覆盖恒等、平移、缩放、透视、多片段和跨页正常映射。
2. 覆盖缺页、不可逆矩阵、低质量、退化多边形、严重裁切、越界和跨题重叠。
3. 断言任何失败均在学生答案模型调用前产生 `mapping_needs_review`。

**验证：** 运行三个测试文件，新增失败关闭场景应先失败。

### T24：实现版本化批量映射与质量校验

**文件：** `backend/homework_judge/alignment/geometry.py`、`backend/homework_judge/alignment/regions.py`、`backend/homework_judge/alignment/models.py`、`backend/homework_judge/config.py`

**依赖：** T23

**步骤：**

1. 实现有限/可逆检查、多边形面积、页内裁切、可见比例和相交比例。
2. 批量映射 frame set 全部有效片段并保留 frameRegionId、原页 polygon、外接框和质量指标。
3. 阈值集中配置；质量问题返回 blocker，不仅设置展示 warning。

**验证：** 重跑 T23，期望正常变换通过、所有异常在识别前阻断。

### T25：改造学生处理为不可变 processing revision

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、`backend/tests/unit/test_student_pipeline.py`

**依赖：** T5、T20、T24

**步骤：**

1. 每次开始处理创建新 revision，固定 frameSetId 和输入 hash。
2. 原子保存配准修订、映射和响应；成功重跑不得删除 `student_pages` 或旧 responses/regions。
3. 提交前比较 frame set、配准 revision 和 current processing pointer；版本变化则放弃本轮结果。

**验证：** 运行学生流水线和数据库测试，断言两代并存且旧评分外键有效。

### T26：为页面级配准校正 API 写测试

**文件：** `backend/tests/integration/test_student_submission_api.py`、`backend/tests/unit/test_alignment_engine.py`

**依赖：** T24、T25

**步骤：**

1. 覆盖修改模板页对应、四组控制点、清除 override 和 expected revision 冲突。
2. 断言一次校正重算该页全部题框，而非只移动一个题。
3. 校正成功后生成新处理/配准修订；旧映射仍可查。

**验证：** 运行上述测试，新增 API 场景应先失败。

### T27：实现页面级配准校正与重映射 API

**文件：** `backend/homework_judge/api/submissions.py`、`backend/homework_judge/alignment/geometry.py`、`backend/homework_judge/jobs/student_pipeline.py`

**依赖：** T26

**步骤：**

1. 校验至少四对非退化控制点及模板/学生页归属。
2. 创建 alignment revision 并从映射阶段重跑受影响页面/提交。
3. 不提供单题学生框 PATCH；错误返回 alignment 层级和 nextAction。

**验证：** 重跑 T26，期望校正、并发和整页重算通过。

### T28：删除学生前端自动补框和范围重构

**文件：** `client/src/features/students/StudentSubmissionsPage.tsx`、`client/src/features/students/StudentPageOverlay.tsx`、`tests/ui/student-overlay.test.tsx`

**依赖：** T7、T22、T24

**步骤：**

1. 删除页面打开即触发题框补算的行为。
2. 删除题框与答案框外接合并、计算题向下一题延伸和同题多片段合并。
3. 逐片段原样绘制服务端多边形，并显示 frame set/alignment revision 和映射 blocker。

**验证：** `pnpm exec vitest run tests/ui/student-overlay.test.tsx`，断言多片段保持独立且不会发补框请求。

### T29：实现学生配准校正界面

**文件：** 新建 `client/src/features/students/StudentAlignmentEditor.tsx`、修改 `client/src/features/students/StudentSubmissionsPage.tsx`、`client/src/lib/api.ts`、`client/src/styles.css`、新建 `tests/ui/student-alignment-review.test.tsx`

**依赖：** T27、T28

**步骤：**

1. 并排显示模板页/学生页，允许选择页对应和四对控制点。
2. 提交 expectedAlignmentRevision，显示重映射进度与问题代码。
3. UI 不提供拖动单题学生框的交互。

**验证：** `pnpm exec vitest run tests/ui/student-alignment-review.test.tsx` 并运行类型检查。

## 第四阶段：题框内空位与配置

### T30：为题框内空位检测契约写测试

**文件：** 新建 `backend/tests/unit/test_blank_detection.py`

**依赖：** T1、T9、T11

**步骤：**

1. 断言检测请求只裁剪当前 confirmed frame set 的完整题框。
2. 参数化 1/2/3/5 个空及同一行、跨行、多个小问和共享上下文，断言运行时生成且仅生成 B1...Bn；替换题号、文本、页码和坐标后关系不变。
3. 断言任意印刷选项标签/文本（字母、数字、中文序号）、图中文字、一个复合框或题框外候选不能产生额外空位；第 11 题固定响应只作为三空真实回归。

**验证：** 运行新测试，期望先因缺 detector 失败。

### T31：实现键控空位检测器

**文件：** 新建 `backend/homework_judge/recognition/blank_detection.py`；修改 `backend/homework_judge/recognition/prompts.py`、`parser.py`、`service.py`

**依赖：** T30

**步骤：**

1. 构建只含确认题框裁图和题面结构的空位检测请求，不读取或比较具体题号。
2. 根据运行时正整数 `n` 按阅读顺序生成 B1...Bn；可靠候选才规范为带页码锚点，不预设上限为三个空。
3. 题框外/非法候选记为 blocker；缺失、低置信或复合共享候选记为 advisory，不自动复制区域来伪造独立锚点。

**验证：** 重跑 T30，期望所有参数化空数/排版通过；第 11 题额外满足恰好三个键且 A 选项无锚点。

### T32：反转旧空位冲突自动确认测试

**文件：** `backend/tests/unit/test_blank_initialization.py`、`backend/tests/unit/test_blank_config_confirmation.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T31

**步骤：**

1. 对任意 `n` 参数化 0/`n-1`/`n`/共享锚点、答案和分值；断言锚点缺失/共享只产生 advisory，而语义空数、答案和分值冲突仍为 blocker。
2. 增加重复键、缺/多标准答案、已提供锚点越框/几何非法和 frameSetId 冲突；确认没有锚点的合法配置可保存。
3. 参数化多组总分和非均匀 Decimal 分值；只有总分而缺逐空分值时均产生 `blank_score_missing`。第 11 题的总分 5 和 1.66/1.66/1.68 仅作为额外回归。

**验证：** 运行三个测试文件，期望新断言在实现调整前失败。

### T33：实现三方一致性与分值门禁

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/homework_judge/grading/blank_config_confirmation.py`

**依赖：** T32

**步骤：**

1. 对运行时任意正整数 `n` 比较题面标记数、逐空答案数及精确键集合，不包含题号或固定空数分支；独立锚点数只形成定位质量 advisory。
2. 要求每空正分且合计等于总分；移除 `allocate_blank_scores()` 的自动确认用途。
3. 即使已有保存行也重新验证完整性，不能以“有一条记录”跳过 readiness。

**验证：** 重跑 T32，期望全部 blocker 和安全自动确认样例通过。

### T34：实现不可变空位配置服务与失效

**文件：** `backend/homework_judge/grading/blank_config_confirmation.py`、`backend/homework_judge/review/invalidation.py`、`backend/tests/unit/test_blank_config_confirmation.py`

**依赖：** T3、T20、T33

**步骤：**

1. 检测/编辑创建新配置版本，确认版本不可覆盖；保存使用 expectedConfigVersion。
2. 只在无 blocker 时 auto_confirm；教师修改并明确确认后为 teacher_confirmed。
3. 配置变化使当前逐空识别、评分和产物过期，迟到识别结果不能提交。

**验证：** 运行配置确认和生命周期测试，覆盖并发 409、版本提升及失效传播。

### T35：接入空位配置 API

**文件：** `backend/homework_judge/api/rubrics.py`、`backend/homework_judge/api/review.py`、`backend/homework_judge/api/grading.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T31、T34

**步骤：**

1. 分离检测、保存草稿和确认配置；返回 readiness signals/blockers/version。
2. 移除 grading_start 静默生成或确认空位配置的行为。
3. 未确认配置只阻断对应题目的识别/判分，并返回明确下一步。

**验证：** 运行 grading API 集成测试，断言旧自动补配置场景改为门禁阻断。

### T36：为逐空配置 UI 写行为测试

**文件：** `tests/ui/grading-config.test.tsx`

**依赖：** T7、T35

**步骤：**

1. 覆盖 B1..Bn、可选锚点状态、标准答案、答案类型、逐空分值和总分。
2. 覆盖三方 signals、具体 blocker、草稿/自动确认/教师确认/stale 状态。
3. 对 n=1/2/3/5 断言缺失/共享锚点不禁用保存，缺分值及空数/答案维度为 n-1/n+1 时确认按钮禁用。

**验证：** `pnpm exec vitest run tests/ui/grading-config.test.tsx`，新增断言应先失败。

### T37：实现严格逐空配置 UI

**文件：** `client/src/features/grading/GradingConfigPanel.tsx`、`client/src/features/review/ReviewPage.tsx`、`client/src/lib/api.ts`、`client/src/styles.css`

**依赖：** T18、T35、T36

**步骤：**

1. 按键编辑答案、变体、类型和分值，保存携带 expectedConfigVersion；现阶段不新增教师手工绘制独立锚点。
2. 显示所有 signals/blockers/advisories 和已有锚点定位，并说明无锚点时使用完整题框共享识别。
3. 删除“开始批改时自动确认”和总分自动均分的 UI/文案。

**验证：** 重跑 T36 并运行 `pnpm run typecheck`。

## 第五阶段：无泄漏逐空识别

### T38：锁定第一阶段请求无答案泄漏

**文件：** `backend/tests/unit/test_student_recognition.py`、`backend/tests/unit/test_recognition_batches.py`

**依赖：** T24、T34

**步骤：**

1. 用 spy 捕获完整模型请求和持久化 request snapshot。
2. 断言只含完整题框图、题目上下文、expectedBlankKeys、锚点和版本。
3. 递归断言不存在标准答案、同义词、正确性、模型判分或逐空分值。

**验证：** 运行两个测试文件，新增断言应先因旧 prompt 输入失败。

### T39：定义 keyed fill-response v2 提示和契约

**文件：** `backend/homework_judge/recognition/prompts.py`、`backend/homework_judge/grading/contracts.py`、`backend/homework_judge/schemas.py`

**依赖：** T38

**步骤：**

1. 删除“每区域恰好一个 segment”的填空约束，改为每个 expectedBlankKey 一个 answer。
2. 定义 `recognizedText/isBlank/confidence/issues/evidenceRefs`，证据必须引用本次输入区域。
3. 明确不允许模型返回标准答案、正确性或分数。

**验证：** 重跑 T38，期望请求和 schema 无答案泄漏。

### T40：为严格键解析和有限重试写测试

**文件：** `backend/tests/unit/test_student_recognition.py`、`backend/tests/unit/test_parser.py`

**依赖：** T39

**步骤：**

1. 对运行时集合 `K={B1...Bn}` 参数化 n=1/2/3/5，覆盖任意乱序仍正确绑定。
2. 对每个 K 覆盖缺任意键、重复任意键、额外任意键、非法 JSON、`isBlank=true` 但非空文本和未知证据 ID；B3/B2/B4 只可作为其中一个示例。
3. 断言第一次结构错只重试一次，第二次失败进入 recognition_needs_review，绝不按换行/下标补齐。

**验证：** 运行两个测试文件，新增严格场景应先失败。

### T41：实现严格逐空解析与模型重试

**文件：** `backend/homework_judge/recognition/parser.py`、`backend/homework_judge/recognition/service.py`

**依赖：** T40

**步骤：**

1. 先验证精确键集合、字段不变量和证据引用，再按 `blankKey` 规范排序。
2. 用错误详情构造一次结构化重试；禁止 regionIndex、数组序号和换行降级。
3. 返回可落库的逐空结果或结构化 needs_review outcome。

**验证：** 重跑 T40，期望所有非法响应失败关闭、乱序响应正确。

### T42：持久化版本化 StudentBlankResponse

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、`backend/tests/unit/test_student_pipeline.py`、`backend/tests/unit/test_student_recognition.py`

**依赖：** T5、T25、T41

**步骤：**

1. 映射通过且配置确认后，从完整题框裁图调用 keyed 识别。
2. 原子保存每空文本、留空、置信、问题、证据、模型/prompt、frame/config/processing 版本。
3. `student_responses.recognized_text` 只生成展示摘要；不再作为填空评分输入。

**验证：** 运行两个测试文件，覆盖幂等保存、共享证据、低置信复核和旧版本 CAS 失败。

### T43：扩展学生详情的逐空识别显示

**文件：** `backend/homework_judge/api/submissions.py`、`shared/contracts.ts`、`client/src/features/students/StudentSubmissionsPage.tsx`、`tests/ui/student-overlay.test.tsx`

**依赖：** T42

**步骤：**

1. 详情 API 返回当前处理修订的逐空答案、状态、问题和版本。
2. 页面按 B 键展示结果，不从整题摘要或区域数推断空位。
3. 映射/识别待复核时显示正确层级和操作入口。

**验证：** 运行提交 API 测试、student overlay UI 测试和类型检查。

## 第六阶段：逐空模型判分与确定性汇总

### T44：锁定评分只能按 blankKey 读取

**文件：** `backend/tests/unit/test_grading_pipeline.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T42

**步骤：**

1. 把旧单 segment 换行 fixture 改为运行时 B1...Bn keyed rows，并参数化 n=1/2/3/5。
2. 断言缺键、重复键、额外键、needs_review 或版本不一致均不进入评分。
3. 删除 regionIndex、segment 顺序和整题 recognized_text 的评分兜底期望。

**验证：** 运行两个测试文件，新增断言应先因旧 `_question_input` 失败。

### T45：改造 grading pipeline 的键控输入

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/homework_judge/grading/contracts.py`

**依赖：** T44

**步骤：**

1. 通过当前 processing/config version 和 `blankKey` 加载 `student_blank_responses`。
2. 严格比较配置、识别和预期键集合；输入快照记录 frame set/config/processing IDs。
3. 删除 segment 下标、单段换行拆分和空值猜测。

**验证：** 重跑 T44，期望只有合法键控输入进入 grading。

### T46：为每空模型判定与工具冲突写测试

**文件：** `backend/tests/unit/test_grading_fill.py`、`backend/tests/unit/test_grading_prompts.py`

**依赖：** T45

**步骤：**

1. 断言每个非可靠留空的 B 键各调用一次模型，精确匹配也不跳过。
2. 断言模型只能返回同一个 blankKey、decision、reason、confidence 和证据，不能提交 score。
3. 覆盖文本、数值、公式工具结果与模型一致/冲突；冲突进入 needs_review。

**验证：** 运行两个测试文件，新增场景应先失败。

### T47：实现每空模型判定

**文件：** `backend/homework_judge/grading/prompts.py`、`backend/homework_judge/grading/fill.py`

**依赖：** T46

**步骤：**

1. 构建每空判分请求，包含当前学生答案、标准答案、规则、题目上下文和工具证据。
2. 严格验证返回键和证据；模型改键、格式错或低置信进入复核。
3. 停止通过答案文本子串猜测共享证据；只接受显式 evidenceRefs。

**验证：** 重跑 T46，期望每空调用、工具冲突和结构校验通过。

### T48：实现后端确定性计分与版本校验

**文件：** `backend/homework_judge/grading/calculation.py`、`backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/unit/test_grading_calculation.py`、`backend/tests/unit/test_grading_pipeline.py`

**依赖：** T33、T45、T47

**步骤：**

1. 忽略/拒绝模型提供的任何分数，对任意长度的空位配置仅按每空 maxScore 和最终状态用 Decimal 计算。
2. `needs_review` 不自动计分；键缺失、版本旧或逐空分值总和冲突时停止汇总。
3. 持久化每空输入/判定/分数和题目汇总审计。

**验证：** 运行两个测试文件，先断言任意 1/2/3/5 空分值向量均可重复汇总，再断言第 11 题回归结果为 1+1+3=5。

### T49：实现教师按键修正与审计

**文件：** `backend/homework_judge/grading/review.py`、`backend/homework_judge/api/grading.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T42、T48

**步骤：**

1. 教师可按 blankKey 修正识别文本，再重新判定该空；也可覆盖最终对错。
2. 参数化选择 K 中任意一个键修改，只使该题当前评分 revision 和产物失效，所有其他键保持原值。
3. 审计记录操作者、原因、前后值、版本和受影响结果。

**验证：** 运行 grading API 测试，断言修改任意选中键时其他键不变且事件完整；B2 仅作为一个回归示例。

### T50：为三层批改可视化写 UI 测试

**文件：** `tests/ui/grading-workspace.test.tsx`、`tests/ui/grading-review-report.test.tsx`

**依赖：** T7、T43、T49

**步骤：**

1. 断言批改页默认显示完整题框，而不是仅 evidence 框。
2. 覆盖“完整题框/空位锚点/识别证据”三层切换、不同图例和样式。
3. 逐空卡显示学生答案、标准答案、判定、分值、版本和教师修正入口。

**验证：** 运行两个 UI 测试文件，新增断言应先失败。

### T51：实现批改页三层叠加与逐空复核

**文件：** `client/src/features/grading/GradingPageOverlay.tsx`、`client/src/features/grading/GradingWorkspacePage.tsx`、`client/src/lib/api.ts`、`client/src/styles.css`

**依赖：** T49、T50

**步骤：**

1. 直接绘制后端完整题框多边形，空位锚点和证据分别单独绘制。
2. 默认题框层开启；图例不再把 evidence 标为题框。
3. 接入按键修正、needs_review 原因和过期版本提示。

**验证：** 重跑 T50，运行 `pnpm run typecheck`。

## 第七阶段：历史恢复与闭环验收

### T52：实现历史任务恢复入口

**文件：** `backend/homework_judge/api/review.py`、`backend/homework_judge/api/submissions.py`、`client/src/features/review/ReviewPage.tsx`、`client/src/features/students/StudentSubmissionsPage.tsx`

**依赖：** T6、T13、T25、T34

**步骤：**

1. 历史任务首次打开显示 legacy frame set/配置待确认，并关闭学生上传与处理。
2. 题框集和配置确认后，提供显式“按新流程重处理”，创建新 processing revision。
3. 旧原图、响应、批改和产物保持可查看并标注旧版本，不再标为 current。

**验证：** 增加 v7→v8 历史任务集成场景，观察确认前阻断、重处理后新旧代并存。

### T53：完成通用多空闭环与第 11 题回归

**文件：** `backend/tests/integration/test_grading_api.py`、`backend/tests/unit/test_student_recognition.py`、`tests/ui/grading-workspace.test.tsx`、oracle fixture

**依赖：** T1、T31、T37、T42、T48、T51

**步骤：**

1. 先用不同题号/文本/坐标的 1/2/3/5 空通用案例跑题框→空位→映射→识别→判分，断言同一链路无样本分支。
2. 再使用人工确认第 11 题 oracle 和固定模型响应，断言 B1=`电荷转移`、B2=`遵守`、B3=`CD`，分值 1/1/3，总分 5。
3. 断言真实样本完整题框覆盖 A-D，A 选项不成为空位；两个 segment/缺 B3 等旧形态均进入复核。

**验证：** 运行通用多空矩阵及 q11 Python/UI 测试；移除 q11 fixture 后通用矩阵仍全部通过。

### T54：完成第 8 题完整题框回归

**文件：** `backend/tests/unit/test_question_frame_validation.py`、`backend/tests/unit/test_alignment_regions.py`、`tests/ui/question-frame-review.test.tsx`、`tests/ui/student-overlay.test.tsx`、oracle fixture

**依赖：** T1、T19、T24、T28、T51

**步骤：**

1. 先用不同题号、页码、选项数量/标签的通用 oracle 验证全部 required sentinels 在框内、全部 forbidden sentinels 在框外；再对第 8 题执行相同契约。
2. 断言映射后 required/forbidden 包含关系不变，批改页范围与确认 frame set 一致。
3. 断言多边形合法、不跨邻题，前端没有整页巨框、外接扩张或向下一题延伸。

**验证：** 运行 q8 相关 Python/UI 测试，期望三处范围一致。

### T55：建立通用性矩阵与生产代码防特判守卫

**文件：** 新建 `backend/tests/unit/test_pipeline_generality.py`、`backend/tests/unit/test_no_fixture_specific_production_rules.py`

**依赖：** T14、T24、T31、T33、T41、T48

**步骤：**

1. 参数化替换题号、题干、答案、页码、坐标和 n=1/2/3/5，断言题框、空位、严格键识别、判分和汇总关系不变；该测试不加载 q8/q11 fixture。
2. 扫描生产目录，禁止导入测试 fixture，禁止出现示例答案、oracle 坐标、fixture 名称或样本哈希。
3. 使用 AST/结构测试禁止对 `detected_number`、`normalized_number`、`question_number` 等题号字段与具体常量比较后改变题框、空位、识别或判分路径。

**验证：** 运行两个新测试；临时排除 q8/q11 fixture 后仍通过，并能在注入一个样本特判分支时可靠失败。

### T56：修订互相冲突的旧测试和旧文档

**文件：** `backend/tests/unit/test_blank_initialization.py`、`backend/tests/unit/test_blank_config_confirmation.py`、`backend/tests/integration/test_grading_api.py`、`docs/acceptance-report.md`、`docs/specs/student-question-overlay/*`、`docs/specs/2026-08-08-fill-config-persistence-fix/*`

**依赖：** T33、T53、T54、T55

**步骤：**

1. 删除/反转“区域冲突不阻断”“开始批改自动确认”“第 11 题自动均分正确”的断言。
2. 将“部分学生页可跳过缺页继续识别”改为 mapping_needs_review 且模型零调用。
3. 在旧文档顶部标注由本 spec 取代的具体规则，不改写历史记录为仿佛从未存在。

**验证：** 执行 `rg -n "region conflicts do not block|1\.66|自动补框|non-blocking" docs backend/tests`，逐条确认剩余命中均带 superseded/反例说明。

### T57：运行完整自动化质量门

**文件：** 全部改动文件

**依赖：** T52、T53、T54、T55、T56

**步骤：**

1. 运行完整 Python/UI 测试、类型检查、lint、构建和 diff whitespace 检查。
2. 单独执行 v7 迁移夹具并确认 `PRAGMA foreign_key_check` 为空。
3. 保存实际通过/失败证据到验收报告；任何失败修复后从对应局部测试开始重跑，再重跑全量。

**验证：**

```powershell
pnpm run test:python -- -q -p no:cacheprovider
pnpm run test:ui
pnpm run typecheck
pnpm run lint
pnpm run build
git diff --check
```

### T58：执行真实浏览器人工验收

**文件：** `checklist.md`（只在执行阶段勾选并记录证据）

**依赖：** T57

**步骤：**

1. 在 Chrome/Edge 的 80%、100%、125%、150% 缩放下检查模板、学生和批改叠加位置。
2. 实际操作拖动、缩放、重画、跨页片段、未保存保护、版本冲突和四点配准校正。
3. 先用任意题号的 1/2/3/5 空合成案例验证通用交互，再用第 8/11 题截图记录真实完整题框、三个空位和逐空判定；未人工执行前对应 checklist 不得预先勾选。

**验证：** 对照 `checklist.md` 逐项记录可观察结果和截图，所有必选项通过后才宣布完成。

### T59：解除独立锚点配置死锁

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/homework_judge/grading/blank_config_confirmation.py`、`backend/tests/unit/test_blank_initialization.py`、`backend/tests/unit/test_blank_config_confirmation.py`、`backend/tests/integration/test_grading_api.py`、`client/src/features/grading/GradingConfigPanel.tsx`、`tests/ui/grading-config.test.tsx`

**依赖：** T33、T35、T37、T42

**步骤：**

1. 将 `missing_blank_anchor`、仅由定位区域数量造成的 `answer_region_count_conflict` 和 `composite_region_shared` 从 blocker 降为 advisory；保留语义空数/答案/分值冲突和已提供锚点非法几何的 blocker。
2. 允许 `region=null` 的 B1..Bn 在答案、键和逐空分值合法时保存并进入 confirmed；学生识别继续给每个键发送同一完整题框证据和可选空锚点。
3. 前端移除“必须配置独立锚点”的误导，显示共享题框说明；增加 1/2/3/5 空通用测试和真实任务第 9 题 API 回归，不调用模型。

**验证：** 运行配置 readiness、确认、grading API、学生识别和 grading-config UI 测试；真实第 9 题在未填逐空分值时只保留 `blank_score_missing` blocker，填入合法分值后可保存确认，模型调用数为 0。

### T60：安全派生配置自动分值并确认

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/homework_judge/grading/blank_config_confirmation.py`、`backend/homework_judge/api/rubrics.py`、`backend/homework_judge/api/review.py`、`backend/homework_judge/jobs/student_pipeline.py`、对应单元/API/UI 测试

**依赖：** T59

**步骤：**

1. 对答案可无歧义拆分且总分有效的任意 `n`，用现有 Decimal 分配器生成精确守恒的默认逐空分值，并把来源标为 advisory 而非 blocker。
2. `prepare_*` 返回安全候选；题目确认、任务完成、学生处理和批改入口原子写入 `auto_confirmed` 不可变版本。已经存在教师/待确认版本时绝不自动覆盖；学生处理允许只保存安全题并为歧义题保留逐题复核结果。
3. 单题题框项确认后允许无锚点自动配置，不再错误要求等待整套冻结；显式教师锚点配置仍要求冻结。只读复核 gate 不再把安全候选误报成“逐空检查并保存”。
4. 保持含糊答案、非法总分、键冲突和教师显式分值冲突失败关闭；实际第 9 题配置版本从 0 变为 `auto_confirmed` v1，模型调用数为 0。

**验证：** 参数化 1/2/3/5 空和不同 Decimal 总分；题框草稿单题确认、学生上传自动补建及部分歧义任务测试；完整 452 项 Python、68 项 UI、TypeScript/Ruff/mypy/构建门禁；实际第 9 题 API 与审计事件。

## 执行顺序

```text
T1 ───────────────────────────────────────────────┐
T2 → T3 ─┬→ T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14
         └→ T4 → T5 ─────────────────────────────┘
T7 → T15 → T16 → T17 → T18 → T19
T13 → T20 → T21 → T22 → T23 → T24 → T25 → T26 → T27 → T28 → T29
T1 + T11 → T30 → T31 → T32 → T33 → T34 → T35 → T36 → T37
T24 + T34 → T38 → T39 → T40 → T41 → T42 → T43
T42 → T44 → T45 → T46 → T47 → T48 → T49 → T50 → T51
T6 + T25 + T34 → T52
T1 + T31 + T37 + T42 + T48 + T51 → T53
T1 + T19 + T24 + T28 + T51 → T54
T14 + T24 + T31 + T33 + T41 + T48 → T55
T53 + T54 + T55 → T56 → T57 → T58
T33 + T35 + T37 + T42 → T59
T59 → T60
```

可并行部分：T4 可与 T3 并行编写失败测试；T15-T19 可在 T13 契约稳定后与映射后端并行；T36 可在 T35 接口契约确定后与 T38 测试并行。共享文件 `schemas.py`、`shared/contracts.ts`、`student_pipeline.py` 和 `grading_pipeline.py` 的任务不得并行编辑。
