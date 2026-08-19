# 就近批注与 AI 错题诊断 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `backend/homework_judge/artifacts/annotation_layout.py` | 局部邻近布局并移除引导线字段 |
| 修改 | `backend/homework_judge/artifacts/annotations.py` | 删除引导线绘制 |
| 新增 | `backend/homework_judge/artifacts/error_analysis.py` | AI 错题诊断契约、提示、请求和校验 |
| 修改 | `backend/homework_judge/artifacts/error_report.py` | 用 AI 诊断组装报告，删除旧文本拼接 |
| 修改 | `backend/homework_judge/artifacts/service.py` | 异步编排模型诊断与报告生成 |
| 修改 | `backend/homework_judge/jobs/grading_pipeline.py` | 注入模型客户端并等待生成物服务 |
| 修改 | `shared/contracts.ts` | 移除批注预览引导线字段，补齐诊断预览字段 |
| 修改 | `client/src/features/grading/GradingPageOverlay.tsx` | 删除 SVG 引导线 |
| 修改 | `backend/tests/unit/test_annotation_layout.py` | 标记邻近性与现有状态回归 |
| 修改 | `backend/tests/unit/test_annotations.py` | 无引导线渲染与原图保护 |
| 新增 | `backend/tests/unit/test_error_analysis.py` | AI 请求内容和严格输出校验 |
| 修改 | `backend/tests/unit/test_error_report.py` | AI 诊断字段和 PDF 渲染 |
| 修改 | `backend/tests/unit/test_grading_pipeline.py` | 异步生成物注入与调用回归 |
| 修改 | `backend/tests/unit/test_grading_review.py` | 异步生成物服务调用适配 |
| 修改 | `backend/tests/integration/test_grading_api.py` | 成功、失败、重试和预览集成 |
| 修改 | `tests/ui/grading-workspace.test.tsx` | 无引导线前端契约与渲染回归 |
| 修改 | `docs/specs/2026-08-12-local-annotation-ai-error-analysis/checklist.md` | 记录实际验收证据 |

## T1：为局部批注布局补充失败测试

**文件：** `backend/tests/unit/test_annotation_layout.py`

**依赖：** 无

**步骤：**

1. 增加页面中部、四个边缘和超宽答案框案例。
2. 断言对勾完整在页面内，标记框与答案框的最短距离不超过局部阈值。
3. 增加邻近候选被占用的案例，断言回退仍在答案邻域而非页面边栏。
4. 断言生成的标记结构中不存在引导线。
5. 保留满分、零分、部分分、待复核和教师复核无位置的状态回归断言。

**验证：** 单独运行 `test_annotation_layout.py`；新邻近性测试应先暴露当前远端回退和 `lead_line` 行为。

## T2：实现答案附近的确定性标记布局

**文件：** `backend/homework_judge/artifacts/annotation_layout.py`

**依赖：** T1

**步骤：**

1. 从 `AnnotationMark` 删除 `lead_line`。
2. 用右、左、上、下的局部候选替换当前页面边栏回退。
3. 给候选计算页内性、冲突和锚点距离，优先无遮挡且最近的位置。
4. 当标准尺寸没有合适位置时逐级缩小标记，再选择最接近锚点的页内候选。
5. 确保错误圈、部分分和待复核规则不变。

**验证：** 重跑 T1；所有位置、距离、页内边界和状态断言通过。

## T3：移除所有后端引导线绘制

**文件：** `backend/homework_judge/artifacts/annotations.py`、`backend/tests/unit/test_annotations.py`

**依赖：** T2

**步骤：**

1. 删除 `_draw_shape` 中引导线分支。
2. 更新测试标记夹具，不再构造引导线。
3. 使用具有明显答案框和标记的测试图生成批注页，检查连接路径上的像素保持背景色。
4. 保留原图哈希不变、页面尺寸不变、PDF 可打开和 `marks.json` 可读取的断言。

**验证：** 运行 `test_annotations.py`，确认无连接线像素且生成物完整。

## T4：定义 AI 错题诊断契约与提示词

**文件：** `backend/homework_judge/artifacts/error_analysis.py`

**依赖：** 无

**步骤：**

1. 定义版本化提示词、受控错误类型、题目输入、逐题输出和整卷输出模型。
2. 提示模型只解释最终评分事实，不得修改分数，不得输出内部评分点键。
3. 明确“计算不认真”只能在公式方法正确但算术、符号、抄写或验算出现局部偏差时使用；证据不足必须选择“无法可靠归因”。
4. 约束错误原因、知识薄弱点、已掌握内容和建议的内容与长度。
5. 明确禁止输出完整标准答案和完整解题过程。

**验证：** 导入模块并通过 Python 编译；契约拒绝非法错误类型、空字段和超长字段。

## T5：构造不含旧批改理由的诊断请求

**文件：** `backend/homework_judge/artifacts/error_analysis.py`、`backend/tests/unit/test_error_analysis.py`

**依赖：** T4

**步骤：**

1. 从非满分题提取题目、答案快照、学生识别作答、最终分数和结构化决策状态。
2. 从评分配置中提取人可理解的评分标准，保留必要的键用于输入关联，但删除所有决策 `reason`。
3. 把教师复核作为事实输入并标注来源，要求模型综合改写。
4. 按题目顺序附加学生作答证据图；不存在有效图片时仍发送明确的文本事实。
5. 在测试中放入唯一的旧批改理由哨兵字符串，断言全部文本消息中不存在该字符串。

**验证：** 运行 `test_error_analysis.py` 的请求构造测试；题目事实、学生作答和证据图存在，旧理由哨兵不存在。

## T6：实现模型调用和一一对应校验

**文件：** `backend/homework_judge/artifacts/error_analysis.py`、`backend/tests/unit/test_error_analysis.py`

**依赖：** T5

**步骤：**

1. 使用现有模型客户端以温度零和 JSON 对象模式调用诊断提示。
2. 校验顶层总体分析和逐题列表。
3. 校验输出题目 ID 集合与输入完全相等、顺序可重排但不得缺失、重复或新增。
4. 校验学生可见字段不包含输入中的内部评分点键。
5. 将无效 JSON、结构错误和题目集合错误转换为明确、可重试的应用错误。
6. 增加成功、缺题、重复、未知题、内部键泄露和模型失败测试。

**验证：** 运行 `test_error_analysis.py`，所有成功与拒绝路径通过。

## T7：用 AI 诊断结果重建错题报告数据

**文件：** `backend/homework_judge/artifacts/error_report.py`、`backend/tests/unit/test_error_report.py`

**依赖：** T6

**步骤：**

1. 删除从 `decisions_json.reason` 取文案的函数和按题型返回固定建议的函数。
2. 删除用题干截断生成知识点、用评分点键生成“已经掌握”的行为。
3. 让报告构造函数接收已经校验的模型诊断，按题目 ID 合并最终题号、得分和证据区域。
4. 更新 PDF 标签为“错误类型、错误原因、知识薄弱点、已经掌握、改进建议”。
5. 保留真实作答裁剪、总分、错题数、空错题列表和完整答案禁用校验。

**验证：** 运行 `test_error_report.py`；报告字段来自测试诊断，不出现旧理由或内部评分点键，PDF 可打开且裁图存在。

## T8：把模型诊断接入异步生成物流程

**文件：** `backend/homework_judge/artifacts/service.py`、`backend/homework_judge/jobs/grading_pipeline.py`、相关单元测试

**依赖：** T6、T7

**步骤：**

1. 把现有模型客户端注入 `GradingArtifactService`。
2. 将生成入口改为异步，批注和 PDF 文件工作通过线程执行，模型诊断直接等待。
3. 扩展 `_question_rows`，读取学生最终识别文本和所需诊断事实。
4. 在存在非满分题时构造请求、调用模型、校验并生成报告；全满分时跳过模型。
5. 更新批改流水线等待异步生成入口。
6. 更新直接调用生成物服务的测试，保持现有状态迁移与错误位置门禁。

**验证：** 运行 `test_grading_pipeline.py`、`test_grading_review.py` 和 `test_error_report.py`；模型调用次数、全满分零调用及状态变化符合预期。

## T9：保证诊断失败不回退旧模板

**文件：** `backend/homework_judge/artifacts/service.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T8

**步骤：**

1. 模拟模型未配置、超时和结构校验失败。
2. 断言运行进入 `failed`、`retryable=1`，包含诊断阶段错误码和用户可读消息。
3. 断言失败版本没有 current 错题报告，且磁盘上没有由旧理由或固定建议构成的有效报告。
4. 模拟下一次模型返回合法结果并重试，断言报告生成完成。

**验证：** 运行对应集成测试；失败不产出伪报告，重试可完成。

## T10：移除网页预览的引导线契约与渲染

**文件：** `shared/contracts.ts`、`client/src/features/grading/GradingPageOverlay.tsx`、`tests/ui/grading-workspace.test.tsx`

**依赖：** T2

**步骤：**

1. 从 `AnnotationPreviewMark` 删除 `lead_line`。
2. 删除叠加组件中的 SVG `<line>` 分支。
3. 更新 UI 标记夹具。
4. 增加渲染断言：显示对勾、错误圈或部分分时，批注组中不存在 `<line>`。
5. 补齐错题报告预览的新增诊断字段类型。

**验证：** 运行相关 Vitest 文件和 TypeScript 类型检查。

## T11：执行专项与全量回归

**文件：** 所有本次修改文件

**依赖：** T3、T8、T9、T10

**步骤：**

1. 运行批注、错题诊断、错题报告和生成物专项测试。
2. 运行全部 Python 测试和 UI 测试。
3. 运行 Ruff、Mypy、TypeScript 类型检查和项目构建。
4. 运行 Python 编译检查和 Git 空白错误检查。
5. 检查差异，只包含本需求文件和原有用户修改，不改写无关文件。

**验证：** 所有命令退出码为 0；若环境阻止某项验证，在 checklist 中保留未勾选并记录实际原因。

## T12：生成真实样例并完成视觉验收

**文件：** `docs/specs/2026-08-12-local-annotation-ai-error-analysis/checklist.md`

**依赖：** T11

**步骤：**

1. 用包含满分题、计算粗心题、知识缺口题和部分分题的试卷生成批注与报告。
2. 检查每个对勾都贴近答案，整卷没有穿过正文的红色连接线。
3. 检查两类错题的错误类型、具体原因、知识薄弱点和建议明显不同且符合学生作答。
4. 对比报告预览 JSON、网页预览和 PDF。
5. 将实际证据记录到 checklist；未实际执行的人工项不得预先勾选。

**验证：** 对照 checklist 逐项记录可观察结果，所有必选项通过后才宣布完成。

## 执行顺序

```text
T1 → T2 → T3 ──────────────┐
                            ├→ T11 → T12
T4 → T5 → T6 → T7 → T8 → T9
                 └────────→ T10
```
