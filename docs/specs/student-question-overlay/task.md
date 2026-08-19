# 学生试卷整题区域展示与任务删除 Tasks

> **已被部分取代（2026-08-09）：** 本文关于学生侧自动补算/扩张题框、缺页时继续识别、以答案框或外接框代表整题范围的规则，已由 [题框驱动的逐空识别与模型批改 Spec](../2026-08-09-question-frame-blank-grading-pipeline/spec.md) 取代。本文仅保留历史决策记录；新实现必须使用教师确认的完整题框集并对整批映射失败关闭。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `backend/homework_judge/db/database.py` | v5 迁移、整题区域表和状态字段 |
| 修改 | `backend/homework_judge/schemas.py` | 模板与学生整题区域模型 |
| 修改 | `backend/homework_judge/alignment/regions.py` | 模板矩形到学生原页四边形映射 |
| 修改 | `backend/homework_judge/alignment/__init__.py` | 导出整题区域映射接口 |
| 修改 | `backend/homework_judge/recognition/prompts.py` | 新任务与历史补框提示词 |
| 修改 | `backend/homework_judge/recognition/normalizer.py` | 整题区域规范化与校验 |
| 修改 | `backend/homework_judge/recognition/parser.py` | 历史补框响应解析 |
| 修改 | `backend/homework_judge/recognition/consolidator.py` | 跨页整题区域合并 |
| 修改 | `backend/homework_judge/recognition/service.py` | 整题区域识别服务 |
| 修改 | `backend/homework_judge/jobs/student_pipeline.py` | 新学生提交同步映射并保存整题区域 |
| 新增 | `backend/homework_judge/jobs/question_region_pipeline.py` | 历史任务整题区域补全作业 |
| 修改 | `backend/homework_judge/jobs/manager.py` | 精确取消并等待多个后台作业 |
| 修改 | `backend/homework_judge/api/dependencies.py` | 注入区域补全作业 |
| 修改 | `backend/homework_judge/api/submissions.py` | 区域补全入口和学生详情扩展 |
| 修改 | `backend/homework_judge/api/tasks.py` | 永久删除任务接口 |
| 修改 | `backend/homework_judge/files/storage.py` | 严格且受限的任务目录清理 |
| 修改 | `backend/homework_judge/main.py` | 初始化区域补全作业 |
| 修改 | `shared/contracts.ts` | 学生区域状态、区域多边形和删除响应类型 |
| 修改 | `client/src/lib/api.ts` | 学生列表/详情、区域补全、重试和删除调用 |
| 新增 | `client/src/features/students/StudentPageOverlay.tsx` | 原图与 SVG 整题框叠加 |
| 新增 | `client/src/features/students/StudentSubmissionsPage.tsx` | 学生上传、列表、轮询和分页查看 |
| 新增 | `client/src/components/ConfirmDeleteTaskDialog.tsx` | 永久删除二次确认 |
| 修改 | `client/src/features/tasks/TaskListPage.tsx` | 独立删除按钮和任务卡片结构 |
| 修改 | `client/src/features/review/ReviewPage.tsx` | 学生试卷入口 |
| 修改 | `client/src/main.tsx` | 学生试卷路由 |
| 修改 | `client/src/styles.css` | 学生工作台、题框和确认框样式 |
| 修改 | `backend/tests/unit/test_database.py` | v5 迁移和级联测试 |
| 新增 | `backend/tests/unit/test_question_regions.py` | 区域规范化、解析和几何映射测试 |
| 修改 | `backend/tests/unit/test_student_pipeline.py` | 新提交整题区域原子持久化测试 |
| 修改 | `backend/tests/unit/test_job_manager.py` | 多作业取消测试 |
| 修改 | `backend/tests/integration/test_student_submission_api.py` | 补全入口和详情区域测试 |
| 新增 | `backend/tests/integration/test_task_delete_api.py` | 永久删除及隔离测试 |
| 新增 | `tests/ui/student-page-overlay.test.tsx` | SVG 坐标与题号交互测试 |
| 新增 | `tests/ui/student-submissions-page.test.tsx` | 学生页面状态与轮询测试 |
| 新增 | `tests/ui/task-delete.test.tsx` | 删除确认和列表刷新测试 |

## T1：增加 v5 数据库迁移

**文件：** `backend/homework_judge/db/database.py`、`backend/tests/unit/test_database.py`  
**依赖：** 无

**步骤：**

1. 把最新数据库版本提升到 v5。
2. 为 `questions` 增加默认空数组的 `question_regions_json`。
3. 为 `student_submissions` 增加题目区域状态和错误字段。
4. 新建 `student_question_regions` 表、唯一约束、外键和查询索引。
5. 让迁移在字段或表已创建但版本号尚未记录时仍可恢复。
6. 增加 v4 升级、重复迁移、外键级联和旧提交默认状态测试。

**验证：** 运行 `test_database.py`，确认全新数据库和 v4 数据库最终均为 v5，重复迁移不报错，删除学生页会级联删除整题区域。

## T2：定义后端区域模型和共享契约

**文件：** `backend/homework_judge/schemas.py`、`shared/contracts.ts`  
**依赖：** T1

**步骤：**

1. 定义模板归一化整题区域，校验页码、正面积、0～1 范围、置信度和问题数组。
2. 定义学生原页像素点、多边形、外接框和区域状态。
3. 扩展学生提交摘要与详情契约，加入题目区域状态、缺失题目和整题区域数组。
4. 定义任务删除成功响应。

**验证：** 运行 Python 模型测试与 TypeScript 类型检查，确认非法坐标被拒绝，详情契约可表达跨页题的多个区域。

## T3：扩展新任务试卷识别提示

**文件：** `backend/homework_judge/recognition/prompts.py`  
**依赖：** T2

**步骤：**

1. 升级试卷结构提示词版本。
2. 要求每道题同时返回完整 `questionRegions` 和现有 `answerRegions`。
3. 明确完整区域覆盖题干、选项、插图和作答位置，跨页题分片返回。
4. 为历史补框定义独立提示，要求按稳定题目 ID 返回完整区域且不得解题。

**验证：** 提示词单元测试断言版本变化、完整区域字段和“不批改、不猜框”约束存在。

## T4：解析并规范化模板整题区域

**文件：** `backend/homework_judge/recognition/normalizer.py`、`backend/homework_judge/recognition/parser.py`、`backend/tests/unit/test_question_regions.py`  
**依赖：** T2、T3

**步骤：**

1. 接受模型常见的 bbox、xywh 和 0～1000 坐标输入。
2. 统一转换为 0～1 的 `page_number/x/y/width/height/confidence/issues`。
3. 丢弃负面积、越界、非数值和无效页码区域，并保留可理解问题。
4. 解析历史补框 JSON，优先按题目 ID 关联，题号只在唯一时兜底。
5. 覆盖多区域、跨页、非法框和重复题号测试。

**验证：** 运行 `test_question_regions.py`，确认合法区域稳定归一化，非法区域不会进入结果，重复题号不会错绑。

## T5：合并并持久化新任务模板区域

**文件：** `backend/homework_judge/recognition/consolidator.py`、`backend/homework_judge/jobs/pipeline.py`、`backend/tests/unit/test_consolidator.py`  
**依赖：** T4

**步骤：**

1. 在跨批次、跨页题目合并时保留并去重 `questionRegions`。
2. 按页码和几何位置稳定排序区域片段。
3. 试卷识别写入题目时保存 `question_regions_json`。
4. 保持现有答题区域、题干和答案匹配行为不变。

**验证：** 运行合并与流水线测试，确认跨页题保留所有片段且重复批次不生成重复框。

## T6：实现历史模板整题区域识别服务

**文件：** `backend/homework_judge/recognition/service.py`、`backend/tests/unit/test_question_regions.py`  
**依赖：** T3、T4

**步骤：**

1. 增加按模板页面和候选题目调用模型的整题区域识别方法。
2. 传入稳定题目 ID、题号、类型和题干。
3. 调用解析与规范化逻辑，按题目 ID 返回区域和调用元数据。
4. 模型无有效区域时返回可处理结果，不伪造默认框。

**验证：** 使用伪模型响应测试多题、跨页、缺失题目和非法 JSON；确认图片以受支持格式传输且没有裁剪图落盘。

## T7：实现整题区域几何映射

**文件：** `backend/homework_judge/alignment/regions.py`、`backend/homework_judge/alignment/__init__.py`、`backend/tests/unit/test_question_regions.py`  
**依赖：** T2

**步骤：**

1. 把模板归一化矩形转换为模板像素四边形。
2. 使用 `template_to_student` 单应性变换映射四个顶点。
3. 计算学生原页可见四边形和裁剪后的外接框。
4. 根据模板置信度、区域问题和页面对齐质量生成 `aligned/needs_review` 状态。
5. 不调用图片裁剪或文件写入。

**验证：** 使用恒等、平移、缩放和透视矩阵测试顶点方向、外接框和页面边界裁剪；确认映射方向为模板到学生原页。

## T8：在新学生提交中生成整题区域

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、`backend/tests/unit/test_student_pipeline.py`  
**依赖：** T1、T6、T7

**步骤：**

1. 在学生处理阶段读取或补齐模板 `question_regions_json`。
2. 为每个有效模板区域调用几何映射并暂存在当前处理代次中。
3. 缺失或低质量区域记录为需要检查，不生成猜测框。
4. 将区域状态与学生提交现有识别状态分开维护。

**验证：** 合成模板和学生页测试应得到正确原页多边形、题号、页引用和质量状态。

## T9：把新提交整题区域原子落库

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、`backend/tests/unit/test_student_pipeline.py`  
**依赖：** T8

**步骤：**

1. 在最终提交事务中删除旧代次整题区域。
2. 插入新学生页面、作答记录和 `student_question_regions`。
3. 更新题目区域状态、错误字段和提交更新时间。
4. 保持“全部处理成功后才切换新结果”的现有重跑保护。

**验证：** 首次处理保存区域；模拟识别失败或取消后，旧页面、旧作答和旧题框仍完整存在。

## T10：实现历史区域补全作业

**文件：** `backend/homework_judge/jobs/question_region_pipeline.py`、`backend/homework_judge/api/dependencies.py`、`backend/homework_judge/main.py`  
**依赖：** T1、T6、T7

**步骤：**

1. 查找任务内缺少模板整题区域的题目并按模板页调用补框服务。
2. 缓存有效模板区域，保留缺失题目列表。
3. 读取已有学生页的对齐矩阵和页面尺寸，映射所有可用区域。
4. 按学生提交原子替换历史整题区域并更新状态。
5. 处理取消、单个提交失败和任务已删除场景，不写入伪造结果。

**验证：** 单元测试确认旧提交无需重新识别学生答案即可得到题框；缺失对齐矩阵的提交进入失败或需检查状态。

## T11：扩展学生提交 API

**文件：** `backend/homework_judge/api/submissions.py`、`backend/tests/integration/test_student_submission_api.py`  
**依赖：** T2、T9、T10

**步骤：**

1. 列表响应加入题目区域状态摘要。
2. 详情响应加入区域状态、缺失题目和整题区域数组。
3. 区域响应包含学生原页像素多边形、外接框和模板归一化框。
4. 增加任务级区域补全启动接口，使用稳定作业键复用重复请求。
5. 保持文件路径、哈希和模型原始响应不对浏览器暴露。

**验证：** 集成测试覆盖空区域、处理中、完成、需检查、跨页多区域和重复启动补全请求。

## T12：增加后台作业精确取消能力

**文件：** `backend/homework_judge/jobs/manager.py`、`backend/tests/unit/test_job_manager.py`  
**依赖：** 无

**步骤：**

1. 增加接收精确作业键集合的取消方法。
2. 在锁内取得目标任务快照并发出取消，在锁外等待所有目标结束。
3. 保留现有比较后移除逻辑，防止旧回调删除同键新作业。
4. 不存在或已完成的键按成功跳过。

**验证：** 测试取消任务级、多个学生级和区域补全作业时只影响目标键；替换作业不会被旧回调移除。

## T13：让任务目录清理严格失败并限制范围

**文件：** `backend/homework_judge/files/storage.py`、`backend/tests/integration/test_task_delete_api.py`  
**依赖：** 无

**步骤：**

1. 对 `uploads/pages/tmp` 分别解析目标任务目录和允许根目录。
2. 拒绝空任务 ID、路径穿越和不在根目录下的目标。
3. 删除存在的目标目录，不再静默忽略文件系统错误。
4. 保持不存在目录可以安全跳过。

**验证：** 临时数据目录测试确认只删除指定任务；路径越界和模拟删除失败会抛出明确错误。

## T14：实现永久删除任务 API

**文件：** `backend/homework_judge/api/tasks.py`、`backend/tests/integration/test_task_delete_api.py`  
**依赖：** T12、T13

**步骤：**

1. 增加 `DELETE /tasks/{taskId}`。
2. 查询任务和学生提交 ID，构造任务、区域补全和学生处理的精确作业键。
3. 取消并等待目标作业，再执行受限文件清理。
4. 在事务中删除任务，依赖外键级联清理所有关联数据。
5. 返回删除成功结构；不存在、取消失败和文件失败返回明确错误。

**验证：** 集成测试确认活动作业先停止、目标文件和数据消失、详情返回 404、其他任务及其文件不变；取消确认不在后端测试范围内。

## T15：补齐前端 API 调用与路由

**文件：** `client/src/lib/api.ts`、`client/src/main.tsx`、`client/src/features/review/ReviewPage.tsx`  
**依赖：** T2、T11、T14

**步骤：**

1. 增加学生列表、详情、学生处理重试、整题区域补全和任务删除调用。
2. 注册 `/tasks/:taskId/students` 页面路由。
3. 在任务审核页加入“学生试卷”入口，不改变现有审核操作。

**验证：** TypeScript 类型检查通过；路由测试可从审核页进入对应任务的学生页面。

## T16：实现原图 SVG 题框组件

**文件：** `client/src/features/students/StudentPageOverlay.tsx`、`tests/ui/student-page-overlay.test.tsx`  
**依赖：** T2、T11

**步骤：**

1. 使用学生原页宽高创建 SVG `viewBox`。
2. 只绘制当前学生页面的多边形和题号标签。
3. 同一题的多区域共享选中状态和可访问名称。
4. 点击题框或标签更新选中题目，不赋予对错颜色和语义。
5. 对缺失图片尺寸或无区域页面显示中性提示。

**验证：** UI 测试断言多边形顶点原样进入 SVG、跨页区域被正确过滤、选择状态同步且没有正确/错误标记。

## T17：实现学生试卷工作台

**文件：** `client/src/features/students/StudentSubmissionsPage.tsx`、`tests/ui/student-submissions-page.test.tsx`  
**依赖：** T15、T16

**步骤：**

1. 加载任务信息和学生提交列表，提供学生姓名、学号和试卷文件上传。
2. 支持选择学生、上一页/下一页以及原图题框展示。
3. 对上传、对齐、识别、失败和完成状态进行轮询与文字展示。
4. 失败提交提供重试；历史区域状态为 `pending` 时自动触发一次补全并轮询。
5. 区域缺失、低质量和补全失败时显示中性文字提示。

**验证：** UI 测试模拟上传到完成、失败重试、历史补全和多页切换；确认请求不会因重新渲染无限重复。

## T18：完成学生页面响应式样式

**文件：** `client/src/styles.css`  
**依赖：** T16、T17

**步骤：**

1. 布局学生列表、原图画布、页码控制和状态区。
2. 让图片与 SVG 绝对重合并共同比例缩放。
3. 为默认、选中、需检查题框提供中性样式和文字图例。
4. 保证 1280×720 下主要控件可达，长页可滚动。

**验证：** 运行前端构建，并在 1280×720 与更宽窗口检查图像、题框、滚动和翻页控件没有遮挡。

## T19：实现永久删除确认对话框

**文件：** `client/src/components/ConfirmDeleteTaskDialog.tsx`、`tests/ui/task-delete.test.tsx`  
**依赖：** T15

**步骤：**

1. 显示任务标题和将被永久删除的数据范围。
2. 提供取消和确认永久删除按钮，并管理提交中状态。
3. 支持 Escape 取消、初始焦点和可访问对话框名称。
4. 删除失败时保持对话框或任务可见并展示错误。

**验证：** UI 测试确认取消不调用 API、确认只调用一次、提交中禁止重复点击、失败信息可见。

## T20：把删除入口接入任务列表

**文件：** `client/src/features/tasks/TaskListPage.tsx`、`tests/ui/task-delete.test.tsx`  
**依赖：** T19

**步骤：**

1. 把任务卡片改为非嵌套交互结构，保留明确的打开任务入口。
2. 为每张任务卡添加独立删除按钮并打开确认对话框。
3. 删除成功后使任务列表查询失效并移除卡片。
4. 删除按钮操作不得触发卡片导航。

**验证：** UI 测试确认打开任务、取消删除、确认删除和错误回退互不干扰，页面不存在按钮嵌套链接警告。

## T21：执行后端完整回归

**文件：** `backend/tests/**`  
**依赖：** T1-T14

**步骤：**

1. 运行全部后端测试。
2. 运行 Ruff、mypy 和 Python 编译检查。
3. 修复区域、迁移、取消或删除引入的所有回归。
4. 使用带 OpenCV 的运行环境单独执行对齐与整题区域映射测试。

**验证：** 后端测试、Ruff、mypy、编译检查和 OpenCV 对齐测试全部通过；只有明确记录的环境依赖可以跳过。

## T22：执行前端完整回归

**文件：** `client/src/**`、`tests/ui/**`、`shared/contracts.ts`  
**依赖：** T15-T20

**步骤：**

1. 运行 TypeScript 类型检查和全部 Vitest 测试。
2. 运行前端生产构建。
3. 修复路由、查询轮询、对话框和 SVG 叠加相关回归。
4. 确认界面中没有对勾、叉号、正确、错误或评分颜色。

**验证：** TypeScript、Vitest 和生产构建全部通过；学生页面和任务列表可由真实路由打开。

## 执行顺序

```text
T1 → T2 ─┬→ T3 → T4 → T5 → T6 ─┬→ T8 → T9 ─┐
         └────────────→ T7 ───────┤           ├→ T11 → T15 → T16 → T17 → T18
                                  └→ T10 ─────┘                    └→ T19 → T20

T12 ─┐
     ├→ T14 → T15
T13 ─┘

T1-T14 → T21
T15-T20 → T22
```
