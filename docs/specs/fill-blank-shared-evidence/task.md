# 多填空共享证据与教师审核优先 Tasks

## 当前基础

多填空内容匹配、共享证据补齐、历史填空结果水合及逐空证据持久化已经完成并通过测试。本轮不重复实现这些能力，重点修复“教师确认仍被评分审计拦截”和“最后一项复核后报告生成失败”。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `backend/homework_judge/grading/audit.py` | 保持完整审计并提供教师裁决读取能力 |
| 修改 | `backend/homework_judge/grading/review.py` | 审计告警不拦截、教师裁决持久化、状态推进 |
| 按需修改 | `backend/homework_judge/api/grading.py` | 最后一项复核后的唯一报告任务触发 |
| 修改 | `backend/homework_judge/artifacts/service.py` | 教师审核题目的无位置降级生成 |
| 验证/按需修改 | `backend/homework_judge/artifacts/annotation_layout.py` | 无可靠位置时不画虚假错误标记 |
| 修改 | `backend/homework_judge/artifacts/error_report.py` | 无截图错题和教师审核说明 |
| 验证/按需修改 | `client/src/features/grading/GradingWorkspacePage.tsx` | 加载、成功、失败、下一项与报告生成反馈 |
| 修改 | `backend/tests/unit/test_grading_audit.py` | 完整告警及教师裁决读取测试 |
| 修改 | `backend/tests/unit/test_grading_review.py` | 教师确认不拦截、审计记录和多复核项测试 |
| 修改 | `backend/tests/unit/test_annotation_layout.py` | 无位置不绘制标记测试 |
| 修改 | `backend/tests/unit/test_error_report.py` | 教师说明及无截图错题测试 |
| 修改 | `backend/tests/integration/test_grading_api.py` | 最后一项复核到报告完成的闭环测试 |
| 修改 | `tests/ui/grading-review-report.test.tsx` | 复核按钮及状态反馈测试 |

## T1：补齐评分审计与教师裁决读取

**文件：** `backend/homework_judge/grading/audit.py`

**依赖：** 无

**步骤：**

1. 保留 `audit_question` 对所有评分问题的完整收集，不改变自动批改阶段行为。
2. 修正依赖矛盾检查的缩进/执行位置，确保无论是否存在错误位置都能产生告警。
3. 增加教师裁决记录识别函数，只接受 `tool=teacher_review` 且版本、状态、必要字段有效的记录。
4. 增加读取最近教师审核说明和全部已覆盖原因的辅助函数，供报告服务复用。

**验证：** 单元测试覆盖分项合计、依赖关系、证据和错误位置告警同时存在，以及有效/无效教师裁决记录的识别。

## T2：教师确认后不再因评分审计失败

**文件：** `backend/homework_judge/grading/review.py`

**依赖：** T1

**步骤：**

1. 保留填空共享证据补齐和教师确认/改判的现有计算顺序。
2. 对候选结果继续执行 `audit_question`，但删除根据审计结果抛出 `GRADING_REVIEW_RESOLUTION_CONTRADICTORY` 的分支。
3. 将全部审计问题转换为结构化 `auditWarnings`，去重后的原因写入 `overriddenReasons`。
4. 生成 `teacher_review` 类型的 `ToolObservation`，保存复核项、原复核原因、动作、教师说明和全部告警。
5. 请求本身无效时仍在事务开始前返回明确错误，不产生部分写入。

**验证：** 当前第 9 题形态即使仍缺 B2/B3 证据也能确认；分项合计和依赖矛盾存在时同样能确认，且告警完整记录。

## T3：原子持久化裁决并逐项推进状态

**文件：** `backend/homework_judge/grading/review.py`

**依赖：** T2

**步骤：**

1. 在关闭复核项的同一数据库事务中更新 `tool_observations_json`。
2. 在 `resolution_json` 和 `review_resolved` 事件中同步保存被覆盖原因与审计告警。
3. 保持同题其他开放复核项不变；只有最后一项关闭后题目才成为 `final`。
4. 刷新全卷 `open_review_count`、总分和结果版本；归零时进入 `generating_annotation`。
5. 验证重复提交同一复核项仍返回“已处理”，不会重复追加记录或重复修改分数。

**验证：** 数据库断言题目、复核项和事件三处记录一致，多原因题目按项关闭，最后一项才推进整卷状态。

## T4：教师审核题目允许无错误位置生成批注

**文件：** `backend/homework_judge/artifacts/service.py`, `backend/homework_judge/artifacts/annotation_layout.py`

**依赖：** T1、T3

**步骤：**

1. 报告服务读取每道题的教师裁决记录。
2. 非满分题无错误位置且没有教师裁决时，继续返回 `ANNOTATION_ERROR_LOCATION_REQUIRED`。
3. 非满分题无错误位置但已由教师审核时，跳过该阻断并继续生成。
4. 确认布局函数在无位置时返回空错误标记，不绘制红圈、连线或虚构坐标。
5. 保持有真实错误位置时的现有批注行为不变。

**验证：** 单元/集成测试同时覆盖“有教师裁决则成功”和“无教师裁决仍失败”。

## T5：错题报告保留教师说明且不伪造截图

**文件：** `backend/homework_judge/artifacts/error_report.py`

**依赖：** T1、T3

**步骤：**

1. 从最近一次有效教师裁决中读取教师说明。
2. 将教师说明以简短、明确的文字合并进错题原因，并遵守现有字段长度限制。
3. 无错误位置时仍生成错题条目，`evidenceRegionId` 保持为空。
4. 渲染阶段在无区域时省略截图，但保留题号、得分、原因、知识点和订正建议。
5. 不输出完整答案或虚构证据。

**验证：** 报告数据和 PDF 渲染测试覆盖有/无教师说明、有/无错误位置组合。

## T6：打通最后一项复核到报告完成

**文件：** `backend/homework_judge/api/grading.py`, `backend/tests/integration/test_grading_api.py`

**依赖：** T3、T4、T5

**步骤：**

1. 验证复核接口提交后重新读取运行状态。
2. 当最后一项关闭并进入 `generating_annotation` 时，通过现有唯一任务键启动报告生成。
3. 多项未完成时不得提前生成；最后一项完成时不得漏触发或重复触发。
4. 集成测试等待任务完成并断言运行状态为 `completed`。
5. 断言批注和错题报告两类产物均为当前结果版本，报告包含无位置教师审核题。

**验证：** 完整 API 流程测试从多个开放复核项逐个确认至两份产物成功生成。

## T7：验证前端按钮与状态反馈

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`, `tests/ui/grading-review-report.test.tsx`

**依赖：** T6

**步骤：**

1. 验证点击确认后按钮立即进入“正在保存复核…”并禁用，避免重复提交。
2. 成功后有剩余项时显示数量并定位下一项。
3. 最后一项成功后显示“正在检查剩余项目并生成结果文件”。
4. 请求/运行错误时恢复按钮并显示具体失败信息。
5. 若现有实现未满足上述任一项，只做最小前端修复。

**验证：** UI 测试覆盖加载、连续复核、最终生成提示和失败恢复。

## T8：完整回归与本地服务切换

**文件：** 全部修改文件

**依赖：** T1–T7

**步骤：**

1. 运行目标单元测试、API 集成测试和 UI 测试。
2. 运行完整后端测试、完整前端测试、Ruff、mypy、TypeScript 类型检查和生产构建。
3. 使用隔离测试数据库复现当前第 9 题及无错误位置题，不代替教师提交真实答卷。
4. 只读检查真实运行仍处于待复核状态及其复核原因。
5. 精确识别当前项目的本地后端进程，重启到新代码并确认健康检查正常。
6. 从浏览器所用接口做只读版本/行为确认，确保页面不再连接旧服务。

**验证：** 全部测试和构建退出码为 0；新后端健康正常；真实数据未被自动修改。

## 执行顺序

```text
T1 → T2 → T3
 │          ├→ T4 ─┐
 │          └→ T5 ─┼→ T6 → T7 → T8
 └─────────────────┘
```
