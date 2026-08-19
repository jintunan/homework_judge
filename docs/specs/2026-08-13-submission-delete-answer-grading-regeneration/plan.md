# 学生答卷删除与当前题答案/批改设置重生成 Plan

## 架构概览

本次改动由三个相互独立但共享版本失效规则的用例组成：

1. **学生答卷删除用例**：前端确认后调用答卷级删除接口；后端解析答卷所属任务、处理修订和
   批改运行，停止所有精确关联的后台任务，安全清理该答卷的上传、页面和生成物目录，再依靠
   SQLite 外键级联删除数据库数据。删除审计保留在任务级审计表中。
2. **当前题答案/批改设置草稿用例**：生成接口捕获当前题、答案关联、题框和正式配置版本，裁剪
   当前题题框图像并加载相关参考答案页，调用专用视觉提示词，严格校验结构后把输入快照和原始
   草稿保存在现有 `runs` 记录中。生成阶段不修改 `matches`、评分配置、评分细则或学生数据。
3. **草稿应用与填空编辑用例**：应用接口按运行 ID 读取服务器保存的草稿，重新核对捕获版本，
   在一个事务中更新当前题答案与正式批改配置、保留旧版本、标记下游学生处理/批改为失效并
   记录审计。前端手动增删空位使用同一确定性分值分配算法，先本地显示结果，服务端仍作最终校验。

草稿复用现有 `runs` 表，无需新增业务表或数据库迁移：`kind` 使用
`answer_grading_regeneration`，`request_summary_json` 保存不可变输入摘要和并发令牌，
`raw_response_json` 保存模型原始返回及规范化草稿，`stage` 从 `generating` 进入 `preview_ready`，
应用后进入 `applied`。草稿的应用内容只从服务器运行记录读取，客户端只提交运行 ID。

## 核心数据结构与接口

### AnswerGradingDraftCapture

- `runId`、`taskId`、`questionId`：草稿运行与稳定业务对象。
- `questionType`、`questionSnapshotHash`：生成时当前题型及有效题目内容哈希。
- `matchId`、`matchUpdatedAt`、`answerEntryId`：生成时答案关联版本。
- `frameSetId`、`frameSetRevision`、`frameContentHash`、`frameItemRevision`：当前题图像来源版本。
- `gradingConfigVersion`、`blankConfigVersionId`：正式批改配置版本。
- `frozenRubricVersionId`、`frozenRubricContentHash`：计算题当前冻结细则版本（如有）。
- `referenceAnswerPageIds`：本次实际读取的参考答案页，用于运行追踪。

### AnswerGradingDraft

- 公共字段：`questionId`、`questionType`、`standardAnswer`、`explanation`、`maxScore`、`warnings`。
- 单选/多选：规范化后的 `answerOptions`。
- 填空：`blanks[]`，每项含 `blankKey`、`sortOrder`、`maxScore`、`answerKind`、
  `standardAnswers`、`synonyms`；不自动创建单空锚点。
- 计算：`rubricPoints[]`，每项含 `pointKey`、`criterion`、`score`、`sortOrder`、
  `dependencies`，并遵守现有 `FINAL_ANSWER` 及替代解法政策。
- `evidence`：草稿使用了哪些题框片段、答案条目和答案页，只返回来源标识，不泄露文件路径。

### AnswerGradingDraftComparison

- `runId`、`status`、`capture`：预览运行及其版本信息。
- `current`：生成开始时的答案、解析和题型专用正式设置快照。
- `draft`：规范化且通过服务端校验的新草稿。
- `changes`：按字段标识新增、修改、删除，供前端稳定呈现新旧对比。

### HTTP 接口

#### 删除答卷

`DELETE /student-submissions/{submissionId}`

成功返回：

```json
{
  "submissionId": "submission-123",
  "taskId": "task-123",
  "deleted": true,
  "cancelledJobs": 3
}
```

不存在返回 `STUDENT_SUBMISSION_NOT_FOUND`；不安全路径或文件暂存失败返回稳定删除错误，数据库
记录保持可重试。

#### 生成草稿

`POST /questions/{questionId}/answer-grading-drafts`

请求无需接受客户端答案或文件路径。服务端读取当前事实、同步等待单题模型结果并返回
`AnswerGradingDraftComparison`。重复请求创建独立运行，便于比较与审计；模型错误沿用统一错误封装。

#### 应用草稿

`POST /questions/{questionId}/answer-grading-drafts/{runId}/apply`

请求体为空。服务端验证运行属于当前题、状态为 `preview_ready`、未应用过、捕获版本仍为当前，
并确认没有活动中的学生处理或批改，再应用服务器保存的规范化草稿。成功返回新的答案、评分配置
摘要和受影响答卷数；冲突返回 `ANSWER_GRADING_DRAFT_SUPERSEDED`，活动任务返回现有稳定冲突码。

## 模块设计

### 学生答卷删除服务

**职责：** 查询精确删除范围、生成任务键、停止后台任务、验证文件路径、暂存文件、事务删除、
完成文件清理和任务审计。

**对外接口：** 异步接收 `submission_id`，返回答卷 ID、任务 ID、取消任务数与删除状态。

**依赖：** 数据库、运行设置、`JobManager`、文件存储安全路径工具。

删除范围通过数据库事实计算：

- 学生主目录：`uploads/{taskId}/students/{submissionId}`。
- 学生页面目录：`pages/{taskId}/student-{submissionId}`。
- 该答卷所有批改运行目录：`artifacts/{gradingRunId}`。
- 后台任务键：`student:{submissionId}`、`student:{submissionId}:new-flow`、每个
  `student:{submissionId}:processing:{revisionId}`、每个 `grading:{runId}` 与
  `grading-artifacts:{runId}`。

文件删除采用“同卷暂存后提交”策略：所有存在目录先移动到数据目录内专用删除暂存区；任一路径
校验或移动失败时回滚已移动目录且不删除数据库。随后在数据库事务中写任务审计并删除学生主记录；
数据库失败则把暂存目录移回。事务成功后清除暂存区；若最终物理清除失败，文件已经脱离所有可访问
路径并记录清理错误，不恢复已删除答卷。此策略避免先删数据库后留下公开文件，也避免普通文件失败
造成不可重试的数据库丢失。

### 答案和批改设置生成服务

**职责：** 检查题型与重复状态，捕获并发版本，准备最小图文输入，创建运行记录，调用模型，解析、
规范化和验证草稿，保存预览事实。

**模型输入：**

- 当前题有效题号、题干、选项、题型、满分；
- 当前题已保存的答案、解析和正式批改配置；
- 当前题在当前题框集中的全部有序裁剪；
- 已关联参考答案条目的答案、解析及其来源答案页图像；未关联时只使用当前直接答案和当前题图像；
- 题型专用输出约束与现行评分政策。

题框和答案页均由服务端根据数据库 ID 读取并验证位于数据目录内。题框裁剪复用单题重识别的
归一化坐标与页面尺寸校验逻辑，提取为共享的模板图像裁剪帮助模块，避免两个用例产生不同几何语义。

**填空空位决策：** 模型必须逐一列出题面上学生需要写入答案的可见位置，包括子问中的横线和
以选择题形式呈现但需要填入选项字母的答题位置。服务端同时计算题干标记数、模型视觉空位数、
参考答案结构数和已有锚点数：以视觉空位清单为主，但任何信号不一致都会加入预览警告。输出空位
按题面阅读顺序编号，分值使用共享的确定性均分函数。

**严格校验：**

- 题型必须与当前有效题型一致且属于四类支持范围；
- 单选恰好一个合法选项，多选至少两个且只能引用现有选项；
- 标准答案、解析不得为空；满分必须等于当前题满分；
- 填空键和顺序连续、每空至少一个答案、分值为正且合计等于满分；
- 计算评分点通过现有评分政策校验，`FINAL_ANSWER` 占比和总分合法，依赖无未知键/环；
- 输出页面和题框来源只能引用捕获输入。

生成成功把规范化草稿连同原始模型响应保存到运行记录；失败运行记录保留错误、原始响应和用量，
正式数据完全不变。

### 草稿应用服务

**职责：** 从服务器运行记录加载草稿，验证一次性应用和版本一致性，按题型写入正式版本，统一
触发学生下游失效并审计。

应用事务规则：

1. 使用 `ensure_question_context_mutable` 阻止活动中的学生识别或批改提交。
2. 重新计算 `AnswerGradingDraftCapture` 中所有当前版本/哈希；任一不同即拒绝旧草稿。
3. 保留当前 `matches.answer_entry_id` 作为参考答案来源，把生成内容写入教师答案/解析覆盖，匹配
   方法标记为教师应用的模型建议，并按题目原确认状态设置匹配确认状态。
4. 单选/多选只更新正式满分配置；填空通过现有版本服务创建新的教师确认逐空配置；计算题创建
   新的模型来源评分细则版本，经同一政策校验后以本次教师为确认者直接冻结。旧版本不删除。
5. 调用新的通用“答案/评分上下文变更失效”帮助函数，解除该任务所有当前学生处理修订指针，
   把答卷置为待重新处理，把批改运行和生成物标记为失效。由于一个处理修订是全卷一致快照，不能
   只替换其中一道题；历史全量保留。
6. 运行阶段改为 `applied`，写入 `answer_grading_draft_applied` 审计；第二次应用同一运行被拒绝。

若题目原本已确认且草稿全部有效，应用后继续保持确认；原本待确认则不改变。其他题完全不更新。

### 填空配置分值与表单校验

将现有后端 `allocate_blank_scores` 对齐为共享行为，并在前端实现等价的 Decimal 字符串分配帮助函数。
前端不用二进制浮点累加显示业务分值：总分先转换为“分”的整数，前 n-1 空按平均值四舍五入，
最后一空取剩余值，再格式化两位小数。因此 500 分值单位除以 3 得 167、167、166。

`GradingConfigPanel` 的增空和删空均重排 `blankKey`、`sortOrder` 并调用均分；教师随后编辑任意分值时
不再自动覆盖其输入。表单在提交前计算：空位数量、连续键、标准答案、正分值和合计差额，把具体
问题显示在行内。服务端 Pydantic 和版本确认服务继续作为最终权威，并把验证错误映射为可读中文。

### 前端答卷列表与确认对话框

现有学生列表项是整块 `<button>`，不能嵌套删除按钮。重构为列表行容器，内部包含一个选择按钮和
一个带明确无障碍名称的删除按钮。确认对话框沿用任务删除对话框的交互模式，但文案和范围限定为
单份学生答卷。

删除 mutation 成功后：移除该答卷详情缓存、刷新列表、清除历史修订与页码状态，并选择删除项之后
的第一项（没有则选择前一项）；失败保持选择和对话框，显示错误。

### 前端草稿对比对话框

在 `GradingConfigPanel` 顶部加入重生成入口，由 `ReviewPage` 提供当前题框与刷新回调。对话框分为
“当前内容”和“新草稿”两列，并按题型呈现选项答案、逐空表格或评分点表格。生成中禁用重复请求；
取消只丢弃当前显示状态；应用成功后关闭对话框，刷新复核、评分配置和评分细则查询，并显示旧学生
结果已失效的提示。

## 模块交互

### 删除答卷

1. 教师在学生列表点击某一行的删除图标。
2. 对话框展示准确身份和永久删除范围；教师确认。
3. API 查询答卷、处理修订和批改运行，枚举并取消精确任务键。
4. 删除服务验证并暂存该答卷的三个类别文件目录。
5. 数据库事务写任务审计并删除 `student_submissions` 主记录，外键级联删除派生记录。
6. 服务清除暂存文件并返回成功；前端刷新列表并移动选择。

### 生成并应用当前题草稿

1. 教师在受支持题目点击“重新生成答案和批改设置”。
2. 服务捕获题目、匹配、题框和配置版本，创建 `answer_grading_regeneration` 运行。
3. 服务裁剪当前题图像、读取相关答案页，调用模型并严格校验输出。
4. 服务把原始响应和规范化草稿存入运行，返回新旧对比；正式数据仍未变化。
5. 教师检查后点击应用。
6. 应用服务确认没有活动任务且所有捕获版本仍一致，在一个事务中写入答案、正式配置、下游失效、
   运行状态和审计。
7. 前端刷新当前题与答卷门禁；教师按现有流程重新处理各学生答卷。

若步骤 3 失败，只有失败运行记录发生变化。若步骤 4 后任何捕获事实被编辑，步骤 6 拒绝应用，教师
重新生成。若步骤 6 中任何写入或校验失败，整个事务回滚。

## 文件组织

```text
backend/homework_judge/
├── api/
│   ├── answer_grading_drafts.py       # 生成、应用接口
│   ├── router.py                      # 注册新接口
│   └── submissions.py                 # 答卷删除接口接入
├── files/
│   └── storage.py                     # 安全暂存/清理答卷与报告目录
├── grading/
│   ├── answer_grading_generation.py   # 提示词、模型契约、解析与题型校验
│   └── blank_initialization.py        # 确定性逐空均分复用
├── review/
│   ├── answer_grading_drafts.py       # 捕获、生成、应用用例
│   ├── invalidation.py                # 通用答案/评分上下文失效
│   └── question_images.py             # 当前题题框裁剪与答案页输入
├── submissions/
│   └── deletion.py                    # 取消任务、文件暂存、级联删除编排
└── schemas.py                         # 草稿输出与接口契约校验

client/src/
├── components/
│   └── ConfirmDeleteSubmissionDialog.tsx
├── features/
│   ├── grading/
│   │   ├── AnswerGradingDraftDialog.tsx
│   │   ├── GradingConfigPanel.tsx
│   │   └── blank-score-allocation.ts
│   └── students/StudentSubmissionsPage.tsx
├── lib/api.ts
└── styles.css

shared/contracts.ts

backend/tests/
├── integration/
│   ├── test_answer_grading_draft_api.py
│   └── test_student_submission_api.py
└── unit/
    ├── test_answer_grading_generation.py
    ├── test_blank_initialization.py
    └── test_submission_deletion.py

tests/ui/
├── answer-grading-regeneration.test.tsx
├── grading-config.test.tsx
└── student-submission-delete.test.tsx
```

不新增数据库迁移：现有外键级联已覆盖答卷派生记录，现有 `runs` 字段可表达草稿生成、预览和应用。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 删除语义 | 单份永久删除并二次确认 | 符合已确认范围，避免引入未要求的回收站 |
| 活动答卷删除 | 精确取消该答卷相关任务后删除 | 教师能处理卡住任务，且不影响其他学生 |
| 文件一致性 | 安全路径校验 + 同数据目录暂存 + 数据库事务 | 避免文件失败时先丢数据库，也避免成功后文件仍可访问 |
| 数据删除 | 删除主记录并依赖外键级联 | 数据库已建立答卷级级联关系，减少遗漏与手写顺序 |
| 草稿持久化 | 复用 `runs`，不新增表 | 已能存快照、原始响应、用量、错误和阶段，且便于审计 |
| 草稿内容来源 | 应用时只读服务器运行记录 | 防止客户端篡改模型草稿后绕过校验 |
| 模型输入 | 当前题题框裁剪 + 相关参考答案文本/页面 | 视觉空位不能只靠 OCR 下划线计数，且满足参考上传答案的要求 |
| 预览方式 | 只读新旧对比，显式应用 | 保证生成不改正式数据；后续仍可在现有编辑器手工微调 |
| 并发控制 | 捕获多层版本/哈希，应用前全量复核 | 防止晚到草稿覆盖教师之后的题目、答案或设置修改 |
| 填空数判断 | 视觉作答位置为主，多信号冲突预警 | 修复 OCR 只识别两个下划线造成少空的案例 |
| 增删空分值 | 每次按总分确定性均分，尾差给最后一空 | 立即满足总分约束，结果稳定且符合用户确认示例 |
| 计算题应用 | 新建并直接冻结已验证版本 | 教师“应用”就是明确确认，旧冻结版本仍保留历史 |
| 下游处理 | 整个学生处理修订失效，历史保留 | 当前架构以全卷修订保证答案、题框和配置快照一致，不能安全局部替换 |
| 自动重跑 | 不自动启动 | 避免应用一次触发所有学生的模型成本，符合已确认边界 |
