# 派生填空配置与正式批改状态一致性 Tasks

> **已被部分取代（2026-08-09）：** 本文中“区域冲突不阻断”“只有总分时自动均分”“开始批改时静默生成或确认配置”等旧规则，已由 [题框驱动的逐空识别与模型批改 Spec](../2026-08-09-question-frame-blank-grading-pipeline/spec.md) 取代。本文仅保留历史记录；新流程要求 B1…Bn 的题面空位、独立锚点、标准答案和显式逐空分值严格一致。

## 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 修改 | `backend/homework_judge/grading/blank_initialization.py` | 增加纯规则自动确认安全性评估 |
| 新建 | `backend/homework_judge/grading/blank_config_confirmation.py` | 候选准备、批量事务保存、幂等与结构化阻断 |
| 修改 | `backend/homework_judge/api/rubrics.py` | GET 返回自动确认就绪状态，继续保持只读 |
| 修改 | `backend/homework_judge/api/review.py` | 题目确认和任务完成接入统一确认服务 |
| 修改 | `backend/homework_judge/jobs/grading_pipeline.py` | 评分启动前补齐历史缺失配置 |
| 修改 | `backend/tests/unit/test_blank_initialization.py` | 就绪评估规则测试 |
| 新建 | `backend/tests/unit/test_blank_config_confirmation.py` | 服务事务、幂等、批量保护和审计测试 |
| 修改 | `backend/tests/integration/test_api_workflow.py` | 题目确认和任务完成组合测试 |
| 修改 | `backend/tests/integration/test_grading_api.py` | 历史任务评分启动补齐及阻断测试 |
| 修改 | `shared/contracts.ts` | 初始化就绪状态和 blocker 详情契约 |
| 修改 | `client/src/features/grading/GradingConfigPanel.tsx` | 区分可自动确认与必须人工保存 |
| 修改 | `client/src/features/grading/GradingWorkspacePage.tsx` | 显示具体题号并提供返回复核页入口 |
| 修改 | `tests/ui/grading-config.test.tsx` | 面板状态和保存后切换测试 |
| 修改 | `tests/ui/grading-workspace.test.tsx` | 启动阻断详情及导航测试 |
| 修改 | `docs/acceptance-report.md` | 记录本次缺陷证据、测试数量和未执行项 |
| 新建 | `docs/specs/2026-08-08-fill-config-persistence-fix/checklist.md` | 本次独立验收清单 |

## T1：定义纯规则就绪评估结构

**文件：** `backend/homework_judge/grading/blank_initialization.py`

**依赖：** 无

**步骤：**

1. 定义 `BlankInitializationReadiness`，包含 `auto_confirmable` 和稳定 `blocking_reasons`。
2. 为无空位、逐空标准答案为空、空位分值无效、分值和不等于题目满分定义原因代码和中文说明。
3. 保持区域相关 warning 与 blocking reason 分离。

**验证：** Ruff 和 Mypy 检查该模块通过，模块仍不导入数据库、FastAPI 或模型客户端。

## T2：实现并测试安全性评估

**文件：** `backend/homework_judge/grading/blank_initialization.py`、`backend/tests/unit/test_blank_initialization.py`

**依赖：** T1

**步骤：**

1. 实现 `assess_blank_initialization(result, max_score)`。
2. 使用 Decimal 精确校验每空分值和题目满分。
3. 增加安全三空、空答案、无效分值、分值不守恒测试。
4. 增加复合区域共享和区域数量冲突仍可自动确认的回归测试。

**验证：** 运行 `backend/tests/unit/test_blank_initialization.py`，所有旧初始化与新增就绪测试通过。

## T3：定义正式配置确认服务的数据结构和查询

**文件：** `backend/homework_judge/grading/blank_config_confirmation.py`

**依赖：** T2

**步骤：**

1. 定义 Candidate、Blocker、Batch 和 Summary 不可变结构。
2. 实现 camelCase blocker 序列化和聚合错误消息。
3. 实现连接级单题/整任务查询，读取教师覆盖、教师/自动答案、区域、配置头及保存空位。
4. 明确过滤重复题和非填空题。

**验证：** 使用内存/临时数据库构造一题，单题与 task 查询返回相同有效字段。

## T4：实现候选准备与全批次阻断

**文件：** `backend/homework_judge/grading/blank_config_confirmation.py`、`backend/tests/unit/test_blank_config_confirmation.py`

**依赖：** T3

**步骤：**

1. 已有空位时归类为 existing，不执行派生。
2. 无配置时调用初始化和就绪评估，产生 Candidate 或 Blocker。
3. 配置头存在但空位为空时产生 `saved_config_inconsistent`。
4. 批量准备先收集全部结果，不做任何写入。
5. 测试两道安全题加一道歧义题可以一次返回完整 blockers。

**验证：** 新服务单元测试断言准备阶段配置表、空位表和审计表行数均不变。

## T5：实现原子持久化、幂等和审计

**文件：** `backend/homework_judge/grading/blank_config_confirmation.py`、`backend/tests/unit/test_blank_config_confirmation.py`

**依赖：** T4

**步骤：**

1. 实现调用方连接上的批量持久化，写配置头、全部空位和自动确认审计。
2. 保存前在同一事务中重新检查已有空位，防止并发重复写入。
3. 审计 payload 记录题目、trigger、空位数、signals 和 advisory warning codes。
4. 实现评分启动使用的 task 级事务包装函数。
5. 测试成功全批提交、失败零写入、重复执行零新增、已有教师配置逐字段不变。

**验证：** 服务单元测试中配置头、空位和审计数量符合预期，故障注入后事务回滚。

## T6：扩展只读评分配置响应

**文件：** `backend/homework_judge/api/rubrics.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T2

**步骤：**

1. derived 配置调用就绪评估并返回 `autoConfirmable`、`blockingReasons`。
2. saved 和 none 返回稳定默认字段。
3. 更新 API 断言，覆盖安全派生与歧义派生。
4. 连续 GET 前后查询配置、空位和审计行数，证明仍然只读。

**验证：** 评分配置 API 测试通过，安全候选 `autoConfirmable=true`，歧义候选为 false。

## T7：接入题目确认事务

**文件：** `backend/homework_judge/api/review.py`、`backend/tests/integration/test_api_workflow.py`

**依赖：** T5

**步骤：**

1. 在确认题目的既有事务内准备单题批次。
2. blocker 转为 `FILL_BLANK_CONFIG_REVIEW_REQUIRED`，题目确认状态不变。
3. 安全候选先持久化，再更新题目与匹配确认状态。
4. 重复确认已有配置时不更新版本、不增加空位或自动审计。

**验证：** 安全三空确认后 GET 为 saved；歧义题确认失败并返回题号、预期空位数和原因代码。

## T8：接入任务完成的全有或全无门禁

**文件：** `backend/homework_judge/api/review.py`、`backend/tests/integration/test_api_workflow.py`

**依赖：** T5、T7

**步骤：**

1. 把任务、题目、答案和填空配置检查放在同一事务语境中。
2. 在任何持久化前合并现有 blocker 与全部填空 blocker。
3. 有 blocker 时回滚且不保存本批任何候选。
4. 全部通过时批量保存配置并完成任务。

**验证：** 两安全一歧义夹具首次完成零新增；修正歧义答案后二次完成三套配置和任务状态同时提交。

## T9：接入评分启动和历史任务补齐

**文件：** `backend/homework_judge/jobs/grading_pipeline.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T5

**步骤：**

1. `create_run` 读取提交 task ID 后调用 task 级确保函数。
2. 成功后沿用 `_build_inputs` 读取正式保存空位。
3. blocker 时不插入评分运行并返回结构化错误。
4. 重复执行或后台 `run` 再次构建输入时不重复保存/审计。
5. 使用已完成、无填空配置的历史任务夹具覆盖当前缺陷。

**验证：** 历史安全任务创建评分运行成功；歧义任务无评分运行且错误列出全部题号。

## T10：扩展共享前端契约

**文件：** `shared/contracts.ts`

**依赖：** T6

**步骤：**

1. 为 `GradingConfigInitialization` 增加 `autoConfirmable` 和 `blockingReasons`。
2. 定义结构化填空配置 blocker 详情类型。
3. 更新现有 mock 夹具，使 saved、derived、none 状态完整。

**验证：** TypeScript `--noEmit` 通过，不使用 `any` 绕过新字段。

## T11：改造评分配置面板状态说明

**文件：** `client/src/features/grading/GradingConfigPanel.tsx`、`tests/ui/grading-config.test.tsx`

**依赖：** T10

**步骤：**

1. derived 且可确认时显示后续明确动作会自动确认，同时保留立即保存按钮。
2. derived 且不可确认时显示逐空检查并保存，不承诺自动通过。
3. saved 状态不显示派生警告横幅。
4. 更新保存、切题和键盘交互测试。

**验证：** 配置面板 UI 测试分别断言 safe derived、blocked derived 和 saved 三种文案。

## T12：增加评分启动错误引导

**文件：** `client/src/features/grading/GradingWorkspacePage.tsx`、`tests/ui/grading-workspace.test.tsx`

**依赖：** T10

**步骤：**

1. 启动失败时保留 `ApiError` code 和 details，而不是只保存字符串。
2. 对 `FILL_BLANK_CONFIG_REVIEW_REQUIRED` 显示后端题号与逐空检查说明。
3. 提供返回当前任务题目复核页的明确链接。
4. 其他错误继续使用现有 ActionFeedback 行为。

**验证：** UI 测试模拟两道 blocker，断言题号、说明和复核链接均可见。

## T13：补充当前真实第 9—12 题规则回归

**文件：** `backend/tests/unit/test_blank_config_confirmation.py`、`backend/tests/integration/test_grading_api.py`

**依赖：** T5、T9

**步骤：**

1. 固定四题的空位数量、答案、分值和区域 warning 形态。
2. 断言复合区域共享和区域数量冲突均为 advisory，四题全部可自动确认。
3. 断言一次评分启动保存 4 个配置头和 11 个空位，第二次预检不增加版本或审计。

**验证：** 真实形态回归测试不需要连接当前运行库即可稳定复现并通过。

## T14：执行全量质量门与性能回归

**文件：** 无实现文件

**依赖：** T1-T13

**步骤：**

1. 运行 Ruff、Mypy、Python compileall 和完整 Pytest。
2. 运行 TypeScript、完整 Vitest 和 Vite 生产构建。
3. 运行 `git diff --check`。
4. 对当前四题形态执行批量就绪评估基准，确认无模型和网络调用且无可感知延迟。

**验证：** 所有命令退出码为 0；仅允许记录项目既有且与本修复无关的警告。

## T15：完成独立 Checklist 与验收报告

**文件：** `docs/specs/2026-08-08-fill-config-persistence-fix/checklist.md`、`docs/acceptance-report.md`

**依赖：** T14

**步骤：**

1. 按 AC1-AC7 记录自动化证据和数据库事务证据。
2. 标记实际通过项，未执行的真实浏览器项目保持未勾选并说明影响。
3. 在验收报告记录根因、修复行为、测试数量、当前历史任务的正常恢复方式和已知警告。

**验证：** 每条 AC 至少映射一个可运行测试或明确的人工场景，报告不把未执行项写成已通过。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5
                  ├→ T6 → T10 → T11
                  ├→ T7 → T8
                  └→ T9 → T12
                         ↓
                        T13 → T14 → T15
```

T6 可在 T5 完成后与 T7-T9 并行；T11、T12 依赖共享契约 T10。所有实现任务完成并通过各自验证后才进入全量质量门。

## 覆盖检查

| Plan 组件 | Tasks |
| --- | --- |
| 纯规则安全性评估 | T1-T2 |
| 正式配置确认服务 | T3-T5 |
| 只读评分配置 API | T6 |
| 题目确认与任务完成 | T7-T8 |
| 历史任务评分补齐 | T9、T13 |
| 共享契约与前端状态 | T10-T12 |
| 全量验收与文档 | T14-T15 |

所有任务均有明确输入、依赖、文件范围和验证方式，不存在循环依赖或待定占位符。
