# 计算题学生作答自动定位 Plan

## 架构概览

本功能在现有“教师确认题框 → 学生页对齐 → 作答转写 → 自动批改”链路中，
为计算题插入一个独立的视觉定位阶段。教师题框继续是不可变的题目锚点；系统
根据当前题与下一题的锚点生成一个确定性的、可跨页的搜索窗口。视觉 Agent
只能在该窗口的空白模板/学生页配对图像中返回学生新增书写的候选框。

定位结果先经过本地几何校验、坐标投影、排序和去重，再使用现有对齐能力生成
精确证据裁剪并执行转写。计算题批改 Agent 接收这些已验证证据的图像及其转写，
不会直接接收整份答卷。证据图使用捕获的对齐版本把不可变学生原页重放到模板
坐标系，再按已验证的模板框临时裁剪，不落库。
可靠空白可以进入正常批改；定位不确定、部分失败或无
有效证据的情况进入教师复核，且不得由批改模型产生无证据的确定性判定。

本轮复用现有处理版本、学生响应、响应区域和原始识别快照，不新增数据库表，
也不改变前端 API 契约。选择题和填空题保留原路径。

## 核心数据结构

### CalculationSearchFragment

一页内允许视觉 Agent 搜索的确定性片段：

| 字段 | 含义 |
| --- | --- |
| `fragment_key` | 本次请求内稳定且唯一的片段键 |
| `template_page_id` | 空白模板页 ID |
| `student_page_id` | 对齐后的学生页 ID |
| `alignment_revision_id` | 本次处理使用的对齐版本 |
| `page_number` | 一基页码 |
| `x/y/width/height` | 模板页归一化坐标；决定模型可见边界 |
| `sort_order` | 跨页自然阅读顺序 |
| `template_image` | 搜索窗口的空白模板裁剪 |
| `student_image` | 同一窗口的模板坐标系学生裁剪 |

搜索片段在模型请求前已经裁剪，模型无法看到当前题之前或下一题之后的内容。
`template_image` 与 `student_image` 只存在于运行时 DTO；持久化搜索计划使用不含
图像字段的 `CalculationSearchFragmentSnapshot`，只记录 ID、版本和几何信息。

### CalculationSearchPlan

| 字段 | 含义 |
| --- | --- |
| `frame_set_id` | 教师确认题框版本 |
| `question_id` | 当前计算题 ID |
| `next_question_id` | 下一道非重复题 ID；最后一题为空 |
| `submission_last_page_number` | 本次学生答卷实际上传的末页页码 |
| `fragments` | 按页码排列的搜索片段 |
| `issues` | 页码缺失、锚点倒序、题框重叠等结构问题 |

计划只有在片段非空、所有涉及页面都有可靠对齐、下一题边界严格位于当前题
之后时才可交给视觉 Agent。

### CalculationLocalizationRequest

一个有界批次的定位请求，包含题号、题型、题干等题面上下文、题框版本、批次
序号及若干 `CalculationSearchFragment`。请求不得包含标准答案、评分细则、
分值、教师判定或其他学生的内容。

### LocalizedCalculationRegion

| 字段 | 含义 |
| --- | --- |
| `fragment_key` | 所属搜索片段 |
| `model_bbox` | 片段内 0..1000 坐标 |
| `template_bbox` | 确定性换算后的模板页归一化坐标 |
| `confidence` | 定位置信度 |
| `issues` | 字迹归属、裁切、重叠等问题代码 |
| `model_candidate_index` | 原始模型候选序号，供审计使用 |
| `batch_index` / `attempt_id` | 在多批次与重试中唯一定位原始模型输出 |

### CalculationLocalizationResult

聚合全部批次后的结果，包含每个输入片段恰好一条 `located|blank|uncertain` 状态、
按阅读顺序排列的候选区域、可靠空白标志、`evidence_complete`、综合置信度、
阻断/提示问题、批次
原始输出、token 用量、定位提示词版本和模型 ID。只有“每个片段都被原样回报、
全部批次成功、全部片段明确为空、置信度达标且无结构问题”才能形成可靠空白；
其他无候选结果一律需要复核。`evidence_complete=false` 表示缺页、缺对齐、缺批次、
无法解析等结构性不完整；它与“证据完整但定位/转写低置信”严格区分。

## 模块设计

### 计算题定位契约与几何规则

**位置：** `backend/homework_judge/recognition/calculation_localization.py`

**职责：**

- 从已按题目顺序加载的确认题框与对齐页集合构造搜索计划。
- 对视觉 Agent 的候选框执行严格 schema、片段键、有限数值、正面积、边界、
  排序和重复检查。
- 将片段内 0..1000 坐标投影回模板页归一化坐标。
- 聚合多个批次并产生稳定问题代码，不依赖网络、数据库或模型客户端。

**搜索窗口算法：**

1. 使用数据库中的规范题目顺序（`sort_order`）取当前题所有确认片段中阅读顺序
   最早的锚点作为起点；下一题不区分题型。
2. 取下一道非重复题最早的确认片段作为排他终点；最后一题以学生答卷实际上传
   的末页为终点。若上传末页或中间页没有可靠配对/对齐，不得静默缩短到最后一个
   已对齐页，而是标记 `evidence_complete=false` 并进入复核。
3. 所有纵向片段采用半开区间 `[top, bottom)`：起始页从当前锚点顶部搜索到页底；
   中间页搜索整页；终止页从页顶精确搜索到下一题锚点顶部。当前题与下一题同页
   时直接使用二者之间的纵向带。若下一题恰在后续页顶部，省略其零高度终止片段，
   但保留此前所有正高度片段。
4. 每个片段使用整页宽度，允许学生在题干下方或页边空白处演算。
5. 若终点不晚于起点、当前题框侵入下一题边界、范围内页面缺失或对齐不可用，
   不猜测列顺序，返回需要复核的结构问题。
6. 当前题的全部确认片段必须落在搜索窗口内；同页多栏导致题目顺序无法用纵向
   半开区间表达时，不猜测阅读顺序并转教师复核。
7. 搜索片段本身不加 padding；最终候选可使用的识别 padding 必须裁回其父搜索
   片段，不能跨过下一题边界读取像素。

### 定位提示词与解析

**位置：**

- `backend/homework_judge/recognition/prompts.py`
- `backend/homework_judge/recognition/parser.py`

**职责：**

- 新增版本化的计算题作答定位提示词。
- 明确要求比较空白模板与学生图，仅框选学生新增的手写、公式、作图和演算，
  不求解、不评分、不转写印刷文字。
- 严格输出 `windows` 数组；每个输入 `fragmentKey` 必须且只能返回一次，状态只
  能是 `located|blank|uncertain`。`located` 至少包含一个区域，`blank` 不含区域，
  `uncertain` 必须说明问题；每个区域使用所属片段内 0..1000 坐标。
- 根对象只允许 `{windows}`；窗口对象只允许 `fragmentKey/status/confidence/issues/
  regions`；区域对象只允许 `bbox/confidence/issues`。缺字段、额外字段、`isBlank`
  等替代字段、Markdown 包裹或 JSON 后尾随文本均拒绝。
- 解析器只接受首尾空白之外完整且合法的 JSON 对象，不自行修补模型内容；语义
  和几何合法性由纯契约模块统一判断。

### 视觉定位服务

**位置：** `backend/homework_judge/recognition/service.py`

**职责：**

- 按“片段说明 → 空白模板图 → 对齐学生图”的顺序构造多模态请求。
- 调用现有视觉模型客户端并返回规范化结果、原始响应和 token 用量。
- 使用现有模型重试与超时机制，不在服务层吞掉模型错误。
- 不把标准答案、评分点或整份学生答卷加入请求。

搜索片段按现有 `answer_pages_per_batch` 分批；每一页只属于一个批次，避免同页
候选重复。每个批次均携带完整题面上下文，因此跨批次不会丢失题目归属。

### 学生处理流水线

**位置：** `backend/homework_judge/jobs/student_pipeline.py`

**职责：**

- 将计算题从当前“直接使用题框裁剪”分支拆出，建立专用定位流程。
- 使用现有模板整形能力，把搜索计划转换为空白模板/学生页配对裁剪。
- 逐批调用视觉定位服务；单批失败时记录问题并继续处理剩余批次。
- 合并并验证候选区域，再用现有区域映射和裁剪能力生成最终证据图。
- 有候选区域时调用现有学生作答转写；可靠空白时跳过转写，并把实际检查过的
  搜索片段保存为空白负证据；不确定
  或失败时创建 `needs_review` 响应而不是丢失该题。
- 缺页、未对齐尾页、缺批次或解析失败等结构问题设置
  `recognition_evidence_complete=false`；完整证据仅因定位/转写低置信时仍可生成
  建议判定，但强制进入复核。
- 综合定位、对齐和转写置信度；任一阶段低于现有识别复核阈值即进入复核。

定位后的 `student_response_regions` 保存最终正/负证据区域。`raw_recognition_json`
新增 `schemaVersion: 1` 的 `localization` 快照；未知版本只能只读降级，不能猜测
字段语义。快照使用不含图像 bytes 的 snapshot DTO，保存：

- 搜索窗口、下一题边界和题框版本；
- 每个批次的 `batchIndex`、稳定 `attemptId`、状态、模型 ID、提示词版本、用量、
  原始输出和问题；
- 每个候选或空白检查窗口到最终 evidence ID 的映射、证据种类、`batchIndex`、
  `attemptId`、`modelCandidateIndex`、置信度和问题；模型框明确为片段内 0..1000，
  模板框同时记录归一化坐标和模板页像素坐标，学生原页记录像素 bbox，并在透视
  映射非轴对齐时同时记录精确 polygon；
- 可靠空白或需要复核的判定依据。

同一 CAS 事务提交前，校验快照中全部证据映射 ID 的集合与该响应全部
`student_response_regions.id` 完全相等，可靠空白的 `blank_search_window` 也包含
在内。由此可从 `grading_question_results.student_response_id` 与
`evidence_refs_json.region_id` 反查学生响应、处理/题框版本、定位批次、响应区域
和学生原页，无需迁移或改写历史数据库记录。该追溯能力本轮通过后台/数据库审计
验证，不新增前端可视字段。

### 批改证据图像桥接与安全降级

**位置：**

- `backend/homework_judge/jobs/grading_pipeline.py`
- `backend/homework_judge/grading/contracts.py`
- `backend/homework_judge/grading/prompts.py`
- `backend/homework_judge/grading/calculation.py`
- `backend/homework_judge/grading/router.py`

**职责：**

- 为内部 `QuestionGradingInput` 增加默认兼容旧记录的
  `recognition_evidence_complete` 标志；新定位链路必须显式设置。
- 查询 evidence 对应的模板 bbox、模板页、学生原页和本次捕获的 alignment
  revision；把不可变学生原页重放到模板坐标系，按精确 `template_bbox` 同时裁剪
  空白模板图与学生图。禁止使用学生原页的轴对齐 `original_bbox` 作为批改裁剪框，
  因为透视变换后的包围框可能包含已验证 polygon 或下一题边界之外的像素。
- 按 evidence ID 顺序把“区域 ID/转写文本/空白标志”与对应的“模板图/学生图”
  一起发送给计算题判分 Agent，且只允许模型引用当前题 evidence ID。可靠空白的
  `blank_search_window` 也发送 evidence ID、空转写和配对图像。
- 图像裁剪范围严格等于已验证模板框，不重新扩大到搜索窗口或下一题；图像 bytes
  只在调用期间存在，不写入运行快照或数据库。
- 若 `recognition_evidence_complete=false`、没有可靠证据、任一正证据图缺失，或
  任一空白负证据的模板/学生图缺失，则不调用正常判分模型：结果为
  `needs_review/MISSING_EVIDENCE`，每个评分点均为 `unable`，不得出现 `failed` 或
  确定性错误位置。数值 0 仅用于满足现有存储契约的非最终占位，不得作为确定分
  展示、汇总或导出。
- 证据结构完整但定位或转写低置信时可以生成建议判定，同时强制保留教师复核。
- 可靠空白使用已检查窗口的完整配对负证据进入正常计算题评分行为。

### 兼容层与前端

现有 `student_responses`、`student_response_regions`、处理版本、题框版本和批改
证据契约已经能够表达定位结果。本轮不新增 migration，也不改变共享 TypeScript
契约。批改工作台继续根据现有 evidence bbox 展示定位后的区域。

### 自动化测试

**位置：**

- `backend/tests/unit/test_calculation_localization.py`
- `backend/tests/unit/test_student_recognition.py`
- `backend/tests/unit/test_student_pipeline.py`
- `backend/tests/unit/test_grading_pipeline.py`
- `backend/tests/unit/test_grading_calculation.py`
- `backend/tests/unit/test_grading_router.py`

纯契约测试覆盖窗口、坐标、去重、空白与问题归并；服务测试验证提示词隔离和
成对图像顺序；流水线测试使用模拟模型覆盖同页、框内、框外、跨页、最后一题、
下一题截断、长批次、可靠空白、低置信、部分失败和其他题型回归；批改测试验证
对齐重放后的精确模板裁剪、正/负证据图像与转写绑定，以及结构不完整、无证据或
任一证据配对图缺失时不调用判分模型。跨页像素测试验证终止页裁剪下边界精确等于
下一题顶部且边界及其下方像素不可见；审计测试从历史批改记录反查完整定位链路。

## 模块交互

```text
已确认题框 + 当前处理版本的页面对齐
                 |
                 v
        构造当前题搜索计划
                 |
                 v
   生成有界模板/学生页配对片段
                 |
                 v
       按页分批调用视觉定位 Agent
                 |
                 v
   本地校验、投影、排序、去重、合并
        |                |                 |
        | 有候选         | 可靠空白        | 不确定/失败
        v                v                 v
 精确裁剪并转写      保存空白响应       保存需复核响应
        |                |                 |
        +----------------+-----------------+
                         |
                         v
       保存证据区域 + localization 快照
                         |
                         v
     重放捕获的对齐并临时裁剪模板/学生 evidence 图像
                         |
                         v
   图像 + 转写交给计算题批改 Agent / 无证据安全短路
```

## 文件组织

```text
backend/homework_judge/
├── recognition/
│   ├── calculation_localization.py   # 新增：窗口、契约、校验与聚合
│   ├── prompts.py                    # 修改：定位提示词和版本
│   ├── parser.py                     # 修改：严格 JSON 提取
│   └── service.py                    # 修改：多模态定位调用
├── jobs/
│   ├── student_pipeline.py           # 修改：计算题定位、批处理与持久化
│   └── grading_pipeline.py           # 修改：对齐重放并临时加载配对 evidence 图像
└── grading/
    ├── contracts.py                  # 修改：证据完整性内部契约
    ├── prompts.py                    # 修改：图像/转写配对内容
    ├── calculation.py                # 修改：多模态评分点判定
    └── router.py                     # 修改：无证据/缺图安全降级

backend/tests/unit/
├── test_calculation_localization.py  # 新增：纯窗口/候选契约测试
├── test_student_recognition.py       # 修改：视觉服务测试
├── test_student_pipeline.py          # 修改：端到端流水线回归
├── test_grading_pipeline.py          # 修改：对齐重放与配对证据图加载测试
├── test_grading_calculation.py       # 修改：图像/区域 ID 绑定测试
└── test_grading_router.py            # 修改：批改短路测试

docs/specs/2026-08-11-calculation-answer-localization/
├── spec.md
├── plan.md
├── task.md
└── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 教师题框语义 | 仅作为锚点 | 符合真实录题方式，不要求教师标注作答区 |
| 搜索终点 | 下一题最早确认锚点，排他 | 最大化捕获本题作答，同时确定性防串题 |
| 跨页方式 | 一页一个片段，长窗口分批 | 页级证据天然可追溯，避免单次上下文截断 |
| 图像输入 | 空白模板与模板坐标系学生裁剪配对 | 可区分印刷内容与学生新增内容 |
| 模型坐标 | 片段内 0..1000，再本地投影 | 模型接口简单，最终边界由本地代码控制 |
| 模型输出处理 | 严格拒绝/去重并触发复核 | 不信任模型几何，避免越界和静默吞题 |
| 置信度 | 定位、对齐、转写取保守组合 | 任一环节不可靠都必须让教师看到 |
| 批大小 | 复用 `answer_pages_per_batch` | 避免新增用户配置并保持既有资源控制 |
| 持久化 | 现有证据表 + 版本化原始定位快照 | 满足审计且无需迁移、历史回填或前端改造 |
| 可靠空白证据 | 保存已检查搜索片段并在定位快照标记负证据 | 证明模型检查范围并满足现有证据审计 |
| 批改图像 | 重放捕获的对齐版本并按模板 bbox 裁剪模板/学生配对图 | 避免原页轴对齐 bbox 越过验证 polygon 或下一题边界 |
| 结构性证据不完整 | 全部评分点 `unable`，跳过判分模型，0 仅作非最终存储占位 | 不把缺页、缺图或技术失败伪装成错误答案 |
| 完整但低置信 | 允许建议判定并强制复核 | 与结构性缺证据分开，保留可用信息 |
| 非计算题 | 保持原分支 | 限制回归面并满足 F9 |

## Spec 覆盖

| Spec | 设计归属 |
| --- | --- |
| F1-F2 | 搜索计划与跨页片段算法 |
| F3 | 配对图像定位提示词与视觉服务 |
| F4 | 候选映射、最终证据裁剪、转写和多模态批改输入 |
| F5 | 本地边界校验与下一题排他终点 |
| F6 | 空白聚合、问题代码和复核降级 |
| F7 | 处理版本、证据区域和 localization 快照 |
| F8 | 每次处理版本重新定位且题框只读 |
| F9 | 非计算题分支隔离和回归测试 |
| N1 | 无证据批改短路和部分失败处理 |
| N2 | `answer_pages_per_batch` 分批及结果聚合 |
| N3 | 纯契约模块的坐标校验、投影和去重 |
| N4 | 原页坐标、版本 ID、模型/提示词版本快照 |
| N5 | 搜索窗口预裁剪后再发送模型 |
| N6 | 无 schema/API 变更，历史记录只读兼容 |
| N7 | 模拟模型与纯函数测试 |
| N8 | 题框内作答和全量回归测试 |
