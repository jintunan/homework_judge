# 学生答卷识别并发与计算题单次识别实施任务

## 执行约束

- 必须按任务依赖顺序实施；每个行为变化先补失败测试，再修改实现使测试通过。
- 不修改数据库表结构、前端、评分规则或历史数据。
- 不重置、覆盖或整理与本功能无关的工作区改动。
- `backend/homework_judge/recognition/prompts.py`、`service.py` 和计算题集成测试已有用户改动，修改时只应用窄范围补丁并逐文件核对差异。
- 所有新并发都必须继续经过全局模型信号量，不允许直接绕过 `DashScopeClient.chat`。
- 每完成一项任务，都在后续 `checklist.md` 中记录测试命令与结果。

## 任务依赖

```text
T01 基线确认
 ├─ T02 配置与题目并发调度
 └─ T03 快速协议与解析
       └─ T04 快速结果标准化
             └─ T05 识别服务快速入口
                   └─ T06 计算题回退编排
                         └─ T07 快照、日志与原子提交验证
                               └─ T08 集成与性能验收
                                     └─ T09 文档和最终回归
```

T02 与 T03/T04 在代码层相互独立，但本次按编号顺序实施，减少同时修改多个核心路径的风险。

## T01：确认工作区与现有行为基线

**目标**：在修改前明确现有用户改动和相关测试状态，避免把已有问题误判为本次回归。

**操作**：

- [ ] 记录目标文件的工作区状态和现有差异，只读检查，不执行重置或清理。
- [ ] 运行现有计算题定位、学生识别、学生管线和计算题集成测试。
- [ ] 记录修改前失败项；如果失败与本功能无关，保留证据但不扩大修复范围。
- [ ] 确认现有提交路径仍在全部识别完成后一次性写入正式结果。

**涉及文件**：只读检查，不修改。

**完成标准**：有可复现的基线测试结果和目标文件差异记录。

**对应需求**：N7、N8、AC12。

## T02：加入单份答卷三题并发调度

**目标**：让选择题、填空题、计算题共享每份答卷最多 3 个题目执行槽位，并保持结果顺序和失败语义。

**测试先行**：

- [ ] 在 `backend/tests/unit/test_student_pipeline.py` 增加 6 道模拟题的并发屏障测试，先确认旧串行实现不能达到峰值 3。
- [ ] 增加乱序完成测试，断言返回结果仍按输入题目顺序。
- [ ] 增加题目异常和外层取消测试，断言没有悬挂任务或部分正式提交。
- [ ] 增加计算题与普通题共享同一题目并发上限的测试。

**实现**：

- [ ] 在 `backend/homework_judge/config.py` 增加 `student_recognition_concurrency`，环境变量为 `STUDENT_RECOGNITION_CONCURRENCY`，范围 1..3，默认 3。
- [ ] 在 `StudentPipeline` 中抽取单题工作单元；单题只返回自己的结果，不写共享结果列表。
- [ ] 在 `_recognize_responses` 中创建有界任务，完整收集结果，并按原始输入顺序过滤/组装。
- [ ] 异常时按原始题目顺序选择第一个异常重新抛出；取消时显式取消并等待所有子任务。
- [ ] 保留 `allow_non_calculation`、未配置填空题和无有效区域题目的现有跳过/复核行为。

**涉及文件**：

- `backend/homework_judge/config.py`
- `backend/homework_judge/jobs/student_pipeline.py`
- `backend/tests/unit/test_student_pipeline.py`

**完成标准**：模拟峰值达到 3 且从不超过 3；乱序完成不改变结果顺序；失败不产生正式部分结果。

**对应需求**：F1-F4、F12、N1、N2、N7、AC1-AC3、AC10。

## T03：定义计算题单次定位并转写协议

**目标**：新增严格、答案隔离的计算题快速协议，同时完整保留旧定位协议。

**测试先行**：

- [ ] 在 `backend/tests/unit/test_student_recognition.py` 增加快速协议精确 JSON 解析测试。
- [ ] 覆盖 Markdown 包裹、外围文字、重复字段、错误根字段、非对象窗口和非有限数值。
- [ ] 断言提示词不包含参考答案、分数、评分规则或要求模型解题的内容。
- [ ] 断言可靠空白窗口无需返回区域转写。

**实现**：

- [ ] 在 `prompts.py` 增加 `CALCULATION_RECOGNITION_PROMPT_VERSION`、快速系统提示词和用户提示词。
- [ ] 快速协议窗口保持旧定位字段；每个定位区域增加 `transcription`、`transcriptionConfidence` 和 `transcriptionIssues`。
- [ ] 在 `parser.py` 增加快速协议严格解析入口，拒绝非完整 JSON 和重复字段。
- [ ] 保持 `CALCULATION_LOCALIZATION_PROMPT_VERSION`、旧提示词和旧解析入口不变，供完整回退使用。

**涉及文件**：

- `backend/homework_judge/recognition/prompts.py`
- `backend/homework_judge/recognition/parser.py`
- `backend/tests/unit/test_student_recognition.py`

**完成标准**：快速协议只接受约定结构；旧定位协议测试无回归；提示词维持答案隔离。

**对应需求**：F5、F9、N3、N4、AC4、AC7、AC9。

## T04：实现快速结果的两层标准化与映射

**目标**：独立判断定位是否可复用、转写是否可用，并把转写稳定绑定到本地证据候选。

**测试先行**：

- [ ] 在 `backend/tests/unit/test_calculation_localization.py` 增加定位与转写均有效的标准化测试。
- [ ] 增加定位有效但转写字段缺失、空文本、重复映射、候选错绑和未知片段测试。
- [ ] 增加定位字段缺失、非法 bbox、窗口缺失/重复的测试。
- [ ] 增加低置信度但结构完整的测试，断言结构仍有效但带复核问题。
- [ ] 增加定位区域去重后的转写映射测试，防止被丢弃候选污染最终分段。

**实现**：

- [ ] 增加快速区域转写和快速批次结果数据结构。
- [ ] 从快速窗口中投影旧定位字段，复用现有坐标、归属、状态、去重和批次标准化逻辑。
- [ ] 以 `(fragmentKey, modelCandidateIndex)` 校验每个最终保留区域恰好对应一份转写。
- [ ] 分别产生 `localization_contract_valid` 和 `transcription_contract_valid`。
- [ ] 将结构问题与低置信度/语义不确定问题区分：前者用于选择回退，后者用于 `needs_review`。
- [ ] 从 `recognition/__init__.py` 导出新增类型和标准化入口。

**涉及文件**：

- `backend/homework_judge/recognition/calculation_localization.py`
- `backend/homework_judge/recognition/__init__.py`
- `backend/tests/unit/test_calculation_localization.py`

**完成标准**：定位可复用性和转写可用性能够分别判断；所有有效转写均能唯一映射到最终保留区域。

**对应需求**：F6-F8、N3、N4、AC4-AC6、AC9。

## T05：增加计算题快速识别服务入口

**目标**：通过一次模型调用取得一个计算题批次的定位和转写，并继续使用统一的模型限流、超时和重试。

**测试先行**：

- [ ] 在 `backend/tests/unit/test_student_recognition.py` 增加服务调用测试，断言一次调用包含每个片段的模板图和学生图。
- [ ] 断言单次成功只调用一次 `client.chat` 并返回标准化结果、原始输出和使用量。
- [ ] 断言超大批次、重复片段、非法几何、缺失图片和错误元数据仍在调用模型前失败。
- [ ] 确认旧 `locate_calculation_regions` 行为和测试保持不变。

**实现**：

- [ ] 抽取旧定位与快速入口共用的片段排序、输入校验和配对图文消息构造私有方法。
- [ ] 在 `RecognitionService` 增加快速批次方法，调用快速提示词、严格解析和两层标准化。
- [ ] 返回标准化快速结果、原始模型响应和使用量。
- [ ] 不新增直接 HTTP 调用；继续通过 `self.client.chat` 获取全局信号量、超时和重试保护。

**涉及文件**：

- `backend/homework_judge/recognition/service.py`
- `backend/tests/unit/test_student_recognition.py`

**完成标准**：单批次快速成功只有一次模型调用；新旧服务入口共享相同输入安全门槛和全局限流。

**对应需求**：F5、F12、N5、N6、AC4、AC10。

## T06：在学生管线编排快速路径和两级回退

**目标**：计算题优先单次定位并转写，并按失败类型执行最小范围回退。

**测试先行**：

- [ ] 快速完整成功：断言不调用旧定位和旧转写，路径为 `single_pass`。
- [ ] 定位有效但转写不可用：断言不重新定位，只转写未解决区域，路径为 `transcription_fallback`。
- [ ] 快速请求失败或定位结构无效：断言对应批次调用旧定位并转写，路径为 `full_fallback`。
- [ ] 可靠空白：断言不调用旧转写，路径为 `reliable_blank`。
- [ ] 低置信度但结构完整：断言不触发回退且状态为 `needs_review`。
- [ ] 多批次混合：断言只回退失败批次/区域，成功批次转写不重复请求；最终路径遵循优先级。
- [ ] 回退转写失败：断言有限结束、保留定位证据并进入 `needs_review`。

**实现**：

- [ ] `_recognize_calculation_response` 的每个批次先调用快速入口。
- [ ] 请求失败或定位合同无效时，只对该批次调用旧定位入口。
- [ ] 收集所有定位有效但缺少可用转写的正向证据，整题最多调用一次旧 `recognize_student_response`。
- [ ] 把快速转写和回退转写统一转换为现有全局 `region_index` 分段，按证据顺序合并 `recognized_text`。
- [ ] 继续把可靠空白加入空白证据段，不为其调用转写。
- [ ] 继续使用现有置信度、配准门槛、证据完整性和问题码决定 `recognized`/`needs_review`。

**涉及文件**：

- `backend/homework_judge/jobs/student_pipeline.py`
- `backend/tests/unit/test_student_pipeline.py`

**完成标准**：四种路径调用次数、证据、分段、文本、置信度和状态均符合 Spec；正常单批次计算题比旧流程少一次模型请求。

**对应需求**：F5-F11、N3-N6、AC4-AC9。

## T07：补齐快照、用量、日志和提交兼容

**目标**：让每种路径可追溯，同时不改变 v1 证据消费者和原子提交协议。

**测试先行**：

- [ ] 断言 `raw_recognition.localization.schemaVersion` 仍为 1，原有必需字段全部存在。
- [ ] 断言整题与每批 `recognitionPath`、快速/回退原始输出、错误和使用量可追踪。
- [ ] 断言 `raw_recognition.usage` 等于所有快速、旧定位和旧转写调用用量之和。
- [ ] 断言定位快照证据 ID 集合与保存的区域 ID 集合严格一致。
- [ ] 并发中一题不可恢复失败时，断言处理修订失败且不会提升部分结果。

**实现**：

- [ ] 对 v1 定位快照只增加字段，不升级或改变既有字段语义。
- [ ] 为整题和批次写入路径、协议版本、各阶段原始输出、用量和错误。
- [ ] 增加不含识别正文的结构化耗时/路径日志。
- [ ] 保持 `_validate_localization_evidence`、处理修订和 CAS 提交入口不变；仅在必要时扩充兼容测试，不放宽校验。

**涉及文件**：

- `backend/homework_judge/jobs/student_pipeline.py`
- `backend/tests/unit/test_student_pipeline.py`
- 如需日志测试，使用现有 `backend/homework_judge/observability.py` 接口，不修改日志基础设施协议。

**完成标准**：旧评分管线能够读取新快照；每次运行可区分路径和请求用量；部分结果不会成为正式版本。

**对应需求**：F3、F4、F10、F11、N3、N7、AC2、AC3、AC8。

## T08：完成集成和模拟性能验收

**目标**：验证快速/回退产生的真实持久化证据可继续进入评分，并量化并发与请求数收益。

**测试先行/集成**：

- [ ] 扩充 `test_calculation_localization_e2e.py`：快速成功的 v1 证据能够被评分管线重放。
- [ ] 增加定位有效仅转写回退与完整回退的持久化路径断言。
- [ ] 增加跨页混合批次的原始输出、用量、证据和分段追踪断言。
- [ ] 用 15 题可控延迟模拟模型测量串行基线和三题并发结果。
- [ ] 断言峰值题目数不超过 3、墙钟时间显著下降。
- [ ] 断言 3 道单批次计算题全部快速成功时，总模型请求比旧两段流程减少 3 次。
- [ ] 用全局模型并发 1、2、3 验证实际请求峰值服从更小限制。

**涉及文件**：

- `backend/tests/integration/test_calculation_localization_e2e.py`
- `backend/tests/unit/test_student_pipeline.py`，或新增一个仅使用模拟模型的性能单元测试文件（若现有文件过于臃肿）。

**完成标准**：AC8、AC10、AC11 有自动化证据，且评分重放不回归。

**对应需求**：F10、F12、N3、N5-N8、AC8、AC10-AC12。

## T09：配置落地、文档和最终回归

**目标**：让默认和当前本地环境真正启用三题并行，并完成全部验收记录。

**实现**：

- [ ] 将 `.env.example` 的 `MODEL_CONCURRENCY` 改为 3，增加 `STUDENT_RECOGNITION_CONCURRENCY=3`。
- [ ] 仅将本地 `.env` 的 `MODEL_CONCURRENCY` 从 2 改为 3，并增加题目并发配置；不展示或修改密钥。
- [ ] 在 `README.md` 说明题目级上限与全局模型上限的关系，以及配置变更后需要重启服务。
- [ ] 运行计算题解析/标准化、识别服务、学生管线和集成测试。
- [ ] 运行后端全量测试；记录通过数量、耗时和任何环境性跳过。
- [ ] 检查最终差异，确认没有数据库迁移、前端改动、评分规则变化或无关文件覆盖。
- [ ] 把所有验收证据和结论更新到 `checklist.md`。

**涉及文件**：

- `.env`
- `.env.example`
- `README.md`
- `docs/specs/2026-08-14-student-recognition-performance/checklist.md`

**完成标准**：所有必需验收项通过；默认及当前本地配置可使用三题并行；最终变更范围与 Spec 一致。

**对应需求**：全部需求，重点为 F1、F12、N8、AC10-AC12。

## 完成定义

只有同时满足以下条件，实施任务才可标记完成：

- 单份答卷题目峰值并发可观测地达到 3 且不超过 3。
- 计算题四种路径均有单元测试，并且调用次数符合预期。
- 正常计算题每个批次一次模型调用完成定位和转写。
- 所有新结果继续使用可被评分管线验证的 v1 本地证据。
- 并发异常不会产生正式部分结果或遗留后台调用。
- 相关测试、集成测试和后端全量测试结果已记录。
- 工作区已有用户改动未被覆盖。
