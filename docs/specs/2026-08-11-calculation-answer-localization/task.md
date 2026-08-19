# 计算题学生作答自动定位 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新增 | `backend/homework_judge/recognition/calculation_localization.py` | 搜索窗口、定位契约、候选校验与批次聚合 |
| 修改 | `backend/homework_judge/recognition/__init__.py` | 导出计算题定位公共类型 |
| 修改 | `backend/homework_judge/recognition/prompts.py` | 新增定位提示词、版本和用户上下文 |
| 修改 | `backend/homework_judge/recognition/parser.py` | 提取定位 JSON，不做隐式修复 |
| 修改 | `backend/homework_judge/recognition/service.py` | 发送配对视觉片段并返回规范化定位结果 |
| 修改 | `backend/homework_judge/jobs/student_pipeline.py` | 计算题搜索、分批定位、证据裁剪、转写和持久化 |
| 修改 | `backend/homework_judge/jobs/grading_pipeline.py` | 重放捕获的对齐并临时裁剪模板/学生配对证据图 |
| 修改 | `backend/homework_judge/grading/contracts.py` | 增加内部识别证据完整性契约 |
| 修改 | `backend/homework_judge/grading/prompts.py` | 将 evidence ID、转写和证据图配对发送 |
| 修改 | `backend/homework_judge/grading/calculation.py` | 使用多模态证据逐评分点判定 |
| 修改 | `backend/homework_judge/grading/router.py` | 无可靠空白、无证据或缺图时安全短路 |
| 新增 | `backend/tests/unit/test_calculation_localization.py` | 搜索计划与定位契约单元测试 |
| 修改 | `backend/tests/unit/test_student_recognition.py` | 视觉定位服务与提示词隔离测试 |
| 修改 | `backend/tests/unit/test_student_pipeline.py` | 同页、跨页、边界、空白、失败和回归测试 |
| 修改 | `backend/tests/unit/test_grading_pipeline.py` | 对齐重放和精确模板 bbox 配对图加载测试 |
| 修改 | `backend/tests/unit/test_grading_calculation.py` | 图像、转写与 evidence ID 绑定测试 |
| 修改 | `backend/tests/unit/test_grading_router.py` | 无证据批改短路测试 |
| 新增 | `docs/specs/2026-08-11-calculation-answer-localization/plan.md` | 技术设计 |
| 新增 | `docs/specs/2026-08-11-calculation-answer-localization/task.md` | 实施顺序 |
| 新增 | `docs/specs/2026-08-11-calculation-answer-localization/checklist.md` | 验收清单 |

## T1：定义搜索计划和稳定问题代码

**文件：** `backend/homework_judge/recognition/calculation_localization.py`、
`backend/tests/unit/test_calculation_localization.py`

**依赖：** 无

**步骤：**

1. 定义搜索片段、搜索计划和结构问题的不可变数据结构。
2. 按数据库规范题序从当前题、下一题（不区分题型）确认片段、实际上传页码和
   对齐页生成页级搜索片段。
3. 实现同页终止、跨页中间整页、最后一题延伸到实际上传末页及半开区间规则；
   下一题位于后续页顶部时省略零高度终止片段。
4. 验证当前题全部确认片段都完整落在窗口内；对多栏纵向顺序歧义、锚点倒序、
   题框越界/重叠、缺页、实际尾页未对齐和空窗口返回稳定问题代码。
5. 保证函数只读输入，不修改教师题框对象。

**验证：** 运行新单元测试，观察同页、跨页、最后一题和非法边界的片段坐标及
问题代码均符合 Plan。

## T2：实现候选区域规范化、投影和去重

**文件：** `backend/homework_judge/recognition/calculation_localization.py`、
`backend/tests/unit/test_calculation_localization.py`

**依赖：** T1

**步骤：**

1. 定义定位请求、候选区域、定位结果和批次结果结构。
2. 校验 `fragmentKey`、0..1000 bbox、有限数值、正面积、置信度和 issues；要求
   根、窗口、区域三层对象字段集合精确匹配 Plan，拒绝额外字段。
3. 把候选框确定性投影为模板页归一化坐标。
4. 按页码、上边界、左边界排序；对高重叠候选保留高置信项并记录重复问题。
5. 要求每个输入片段恰好返回一次，定义可靠空白、候选存在、无候选不确定、
   缺键/重键/未知键及状态与区域矛盾的规则。

**验证：** 参数化测试覆盖合法多区域、未知片段、越界、NaN、零面积、重复、
低置信、额外字段和 `status/regions` 语义矛盾；模型输出中不存在 `isBlank` 字段。

## T3：增加严格定位提示词与 JSON 解析

**文件：** `backend/homework_judge/recognition/prompts.py`、
`backend/homework_judge/recognition/parser.py`、
`backend/tests/unit/test_student_recognition.py`

**依赖：** T2

**步骤：**

1. 增加版本化计算题定位 system prompt 和用户上下文序列化函数。
2. 明确只比较模板/学生差异，不求解、不评分、不复制印刷文字。
3. 定义 keyed `windows`、`fragmentKey`、`status`、`confidence`、`issues` 和
   `regions` 的严格输出形状，并要求输入输出键集合完全一致。
4. 增加只接受首尾空白之外完整 JSON 对象的解析入口，不补写缺失字段，并拒绝
   Markdown 代码围栏、前导说明和 JSON 后尾随文本。
5. 测试上下文不含标准答案、同义答案、评分细则或分值。

**验证：** 定向测试断言提示词版本、字段、片段键和答案隔离；非法 JSON 返回
结构问题而不是伪造候选。

## T4：接入多模态视觉定位服务

**文件：** `backend/homework_judge/recognition/service.py`、
`backend/homework_judge/recognition/__init__.py`、
`backend/tests/unit/test_student_recognition.py`

**依赖：** T2、T3

**步骤：**

1. 新增接收一个有界批次的定位服务入口。
2. 按片段顺序追加说明、空白模板图和学生图。
3. 调用现有模型客户端并把原始 JSON 交给纯契约模块校验。
4. 返回规范化结果、原始响应、用量、模型 ID 和提示词版本。
5. 使用 fake client 验证多片段顺序、图片数量和模型错误透传。

**验证：** `test_student_recognition.py` 中的定位服务测试全部通过，且不发出真实
网络请求。

## T5：把搜索计划转换为配对图像片段

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、
`backend/tests/unit/test_student_pipeline.py`

**依赖：** T1、T4

**步骤：**

1. 在计算题专用分支中读取本次处理版本的确认题框和页面对齐。
2. 调用搜索计划构造器，不再把教师题框直接当作计算题答案边界。
3. 使用现有学生页模板整形与区域裁剪能力生成模板/学生配对片段。
4. 将模板页、学生页和 alignment revision ID 绑定到片段元数据。
5. 将运行时带图像片段与不含图像 bytes 的持久化 snapshot DTO 分开。
6. 搜索计划存在结构 blocker 时构造 `recognition_evidence_complete=false` 的需复核
   响应并跳过模型定位。

**验证：** 流水线测试断言题框下方区域进入模型输入、下一题之后像素不进入，
且教师题框数据库记录保持不变。

## T6：实现跨页分批定位与结果聚合

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、
`backend/tests/unit/test_student_pipeline.py`

**依赖：** T2、T5

**步骤：**

1. 按 `answer_pages_per_batch` 对搜索片段做无重叠分批。
2. 逐批调用定位服务并累计原始输出、用量、置信度和问题。
3. 单批模型超时/错误时记录失败并继续剩余批次。
4. 合并全部有效候选，可靠空白只在所有批次明确成功为空时成立。
5. 缺页、未对齐尾页、缺批、解析失败或批次失败设置
   `recognition_evidence_complete=false`；证据完整但低置信单独标记。
6. 上述两类异常都强制最终响应进入复核，但只有结构性不完整禁止正常判分模型。

**验证：** 构造超过批大小的跨页用例，断言首尾页候选均保留；中间一批失败时
其他批次证据仍保存且总体状态为 `needs_review`。

## T7：生成最终证据、转写并保存定位快照

**文件：** `backend/homework_judge/jobs/student_pipeline.py`、
`backend/tests/unit/test_student_pipeline.py`

**依赖：** T6

**步骤：**

1. 将已验证候选转换为现有模板区域格式并生成最终证据裁剪。
2. 有候选时调用现有转写服务；可靠空白时跳过转写；无候选不确定时保存复核响应。
3. 使用定位、对齐和转写的保守组合置信度决定响应状态。
4. 在 `raw_recognition_json.localization` 保存 `schemaVersion: 1`、不含图像的搜索
   计划快照、批次 `batchIndex/attemptId`、模型/提示词版本、原始输出、用量和问题；
   未知版本读取时只读降级。
5. 可靠空白时把已检查的搜索片段保存为负证据，并在定位快照标记
   `evidenceKind=blank_search_window`。
6. 最终候选的识别 padding 必须裁回父搜索窗口，不能读取下一题像素。
7. 每条证据映射记录 `batchIndex/attemptId/modelCandidateIndex`，并明确换算和保存
   模型片段 0..1000、模板归一化、模板像素、学生原页像素 bbox/polygon 坐标。
8. 在同一 CAS 事务提交前，验证快照的全部 evidence ID 集合与该响应全部
   `student_response_regions.id` 完全相等，包括正候选和空白负证据。
9. 保持 `student_response_regions` 的现有坐标与顺序契约，使批改证据无需 API 变更。

**验证：** 查询测试数据库，断言全部正/负 evidence ID 与定位快照集合完全相等，
坐标单位换算正确；并从 `grading_question_results.student_response_id` 和
`evidence_refs_json.region_id` 反查处理版本、题框版本、定位批次、学生页及原始坐标。

## T8：把定位证据图与转写交给批改 Agent 并安全降级

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、
`backend/homework_judge/grading/contracts.py`、
`backend/homework_judge/grading/prompts.py`、
`backend/homework_judge/grading/calculation.py`、
`backend/homework_judge/grading/router.py`、
`backend/tests/unit/test_grading_pipeline.py`、
`backend/tests/unit/test_grading_calculation.py`、
`backend/tests/unit/test_grading_router.py`

**依赖：** T7

**步骤：**

1. 为内部 `QuestionGradingInput` 增加 `recognition_evidence_complete`，旧历史输入默认
   为 true，新定位链路显式传值。
2. 根据每条证据查询模板 bbox、模板页、学生原页和捕获的 alignment revision；
   重放对齐，把学生页变换回模板坐标系，并按精确模板 bbox 临时裁剪空白模板图/
   学生图配对。禁止直接使用学生原页轴对齐 bbox 裁批改图。
3. 严格保持 evidence 顺序；先发送 evidence ID、转写和空白标志，再发送同 ID 的
   模板/学生配对图。可靠空白负证据也发送 ID、空转写和配对图像。
4. 图像 bytes 仅存在于模型调用期间，不放入输入快照、评分快照或数据库。
5. 识别 `recognition_evidence_complete=false`、无可靠 evidence、任一正证据图缺失，
   或任一 `blank_search_window` 配对图缺失的输入。
6. 对上述输入不调用模型：返回 `needs_review/MISSING_EVIDENCE`，每个评分点均为
   `unable`，不得生成 `failed` 或确定性错误位置。数值 0 仅用于满足现有存储契约
   的非最终占位，不得作为确定分展示、汇总或导出。
7. 保持“完整证据但低置信”的建议判定路径和可靠空白正常评分路径，并只允许模型
   引用当前题 evidence ID。
8. 使用会在调用时抛错的 fake client 证明所有安全短路生效。

**验证：** 测试用带透视变换的页面断言模板坐标裁剪精确、下一题像素不可见；模型
请求中的每个正/负 evidence ID 均绑定对应模板/学生图。结构不完整、无证据和任一
配对图缺失时不调用模型且全部评分点 `unable`；完整低置信及可靠空白行为正确。

## T9：覆盖题框内与同页题框外作答

**文件：** `backend/tests/unit/test_student_pipeline.py`

**依赖：** T7

**步骤：**

1. 把现有“计算题扩展到完整题框”回归改为“题框仅作锚点”。
2. 构造学生书写位于题框内的用例，断言仍被定位和转写。
3. 构造学生书写位于题框下方、下一题上方的用例，断言证据框超出教师题框。
4. 构造同页下一题手写，断言不会进入本计算题候选或转写。
5. 使用像素标记验证候选 padding 被父搜索窗口裁切，下一题边界像素不可见。

**验证：** 对应四个场景的数据库 evidence bbox、转写内容和模型调用计数全部正确。

## T10：覆盖跨页、最后一题和边界异常

**文件：** `backend/tests/unit/test_calculation_localization.py`、
`backend/tests/unit/test_student_pipeline.py`

**依赖：** T6、T9

**步骤：**

1. 构造当前页到下一页的连续解答并验证阅读顺序；下一题在终止页中部时，终止
   片段下边界必须精确等于下一题顶部，边界及以下标记像素不可见。
2. 构造多页长答案并验证分批合并不漏首尾页。
3. 构造下一题恰在后续页顶部，断言省略零高度终止片段且保留此前正高度片段。
4. 构造最后一题延伸至实际上传末页；另构造上传尾页未配对/未对齐，断言不静默
   截短且 `recognition_evidence_complete=false`。
5. 构造当前题多片段、同页多栏歧义、缺失中间页、锚点倒序、题框重叠和低质量
   对齐，断言所有当前题片段要么完整纳入，要么以稳定问题安全复核。

**验证：** 定向测试覆盖 AC2、AC3、AC7、AC8、AC13，所有异常均有稳定问题代码。

## T11：覆盖空白、部分失败与其他题型回归

**文件：** `backend/tests/unit/test_student_pipeline.py`、
`backend/tests/unit/test_grading_pipeline.py`、
`backend/tests/unit/test_grading_calculation.py`、
`backend/tests/unit/test_grading_router.py`

**依赖：** T7、T8

**步骤：**

1. 测试全部批次高置信空白时保存 `isBlank=true` 且不调用转写。
2. 测试低置信空白、无候选非空白、候选带问题和单批失败均进入复核。
3. 断言选择题仍使用完整确认题框、填空题仍使用确认空位配置，二者不调用定位服务。
4. 断言可靠空白保存每个已检查窗口的负证据，并把 evidence ID、空转写、模板图/
   学生图交给批改 Agent；审计不把它误认为无证据。
5. 断言重新处理产生新处理版本和新定位快照，但确认题框版本不变。
6. 删除任一正证据或空白负证据的模板/学生图来源，断言正常判分模型不调用、所有
   评分点为 `unable`，且没有 `failed` 或确定性错误位置。

**验证：** 定向回归覆盖 AC5、AC6、AC8、AC10、AC11、AC12。

## T12：执行质量门并填写验收清单

**文件：** `docs/specs/2026-08-11-calculation-answer-localization/checklist.md`

**依赖：** T1-T11

**步骤：**

1. 运行定位、识别、学生流水线和批改路由的定向 Pytest。
2. 运行完整 Pytest、Vitest、Ruff、Mypy、TypeScript 类型检查和生产构建。
3. 运行 Python compileall 与 `git diff --check`。
4. 按实际命令输出逐项勾选 checklist；未执行项目保持未勾选并说明原因。
5. 核对模型请求测试中不存在答案、评分点和窗口外图像。

**验证：** 所有质量命令退出码为 0；AC1-AC13 每项都有自动化证据或明确人工场景。

## 执行顺序

```text
T1 → T2 → T3 → T4
 |          |
 +----------+→ T5 → T6 → T7 → T8
                         |
                         ├→ T9
                         ├→ T10
                         └→ T11
                               |
                               v
                              T12
```

T3 可在 T2 完成后与 T1 的额外边界测试并行；T9-T11 在 T7 完成后可并行编写，
但 T12 只能在全部实现和定向验证通过后执行。

## 覆盖检查

| Plan 组件 | Tasks |
| --- | --- |
| 搜索计划与几何边界 | T1、T5、T9、T10 |
| 候选契约与聚合 | T2、T6、T10 |
| 提示词、解析和视觉服务 | T3、T4 |
| 流水线、证据与审计快照 | T5-T7 |
| 多模态批改输入与安全降级 | T8、T11 |
| 兼容与回归 | T9-T12 |
