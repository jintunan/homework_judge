# 题框驱动逐空批改迁移与上线说明

## 迁移目标

数据库升级到 v8 后，旧任务继续可读，但旧题框、旧空位配置和旧学生结果不会被静默认定为满足新流程。教师必须确认完整题框和逐空配置，再显式创建新的学生处理代次。迁移不删除原始文件、学生页面、旧识别、旧评分或生成物。

## v8 关键对象

| 对象 | 作用 | 当前事实指针 |
| --- | --- | --- |
| `question_frame_sets/items/regions` | 模板完整题框及跨页片段 | 任务当前冻结 frame set |
| `question_blank_config_versions/definition_versions` | B1...Bn、锚点、答案、同义词和逐空满分 | 每题当前 config version |
| `student_processing_revisions` | 一次学生处理捕获的全部输入版本 | submission current processing revision |
| `student_page_alignment_revisions` | 页面对应、变换、质量和教师控制点 | 处理代内每页 current alignment |
| `student_question_regions` | 冻结题框映射到学生原页的结果 | 绑定 processing/frame/alignment |
| `student_blank_responses` | 无答案泄漏阶段的逐空转写 | 绑定 B 键和所有输入版本 |
| `grading_blank_results` | 模型逐空判定与教师覆盖 | 绑定 grading/frame/config/processing |

这些对象使用不可变版本和 current 指针，而不是原地覆盖历史行。后台提交结果时必须再次核对 current 指针和捕获版本。

## 上线步骤

1. 停止新任务写入，并备份数据库与 `data` 原始文件目录。
2. 部署新代码后启动一次应用，让数据库迁移在独占写事务中完成。
3. 运行数据库迁移测试和 `PRAGMA foreign_key_check`；结果必须为空。
4. 先选择一个非生产关键历史任务走恢复流程，确认原图、旧响应、旧评分和产物仍可查询。
5. 教师在模板复核页逐题核对完整题框，修正后确认并冻结。
6. 对每道填空题核对 B1...Bn、锚点、标准答案、可接受答案和逐空分值；存在 blocker 时人工修正后保存。
7. 在学生答卷页选择历史答卷，点击“按新流程重处理”。系统创建新 processing revision，不删除旧版本。
8. 处理完成后核对三层叠加、逐空识别、逐空判定、总分和历史版本切换，再逐步放量。

## 新任务的硬门禁

- 任一有效题缺少教师确认的完整题框：拒绝学生上传和处理，返回 `QUESTION_FRAMES_NOT_CONFIRMED`。
- 任一填空题缺少与当前 frame set 一致的已确认配置：拒绝上传和处理，返回 `BLANK_CONFIGS_NOT_CONFIRMED`。
- 页面缺失、配准质量不足或批量映射不完整：进入 `mapping_needs_review`，答案模型调用数为零。
- 识别返回键集合不等于预期 B 键：有限重试后进入 `recognition_needs_review`，不做位置补齐。
- 判分版本冲突、工具校验冲突或模型不确定：进入教师复核，不使用自由文本分数。

## 历史任务恢复规则

- `source=legacy` 的题框和逐空配置只作为待核对材料；模型置信度为 1.0 也不自动确认。
- 读取任务、打开复核页和读取配置均不得产生确认写入。
- 教师确认后必须显式重处理；普通历史查看不会偷偷更换 current 结果。
- 新旧 processing revision 可并存，详情接口可指定历史 `processingRevisionId`。
- 历史评分页面优先使用评分运行捕获的题框和锚点；缺失历史几何时明确提示，不拿当前坐标冒充。

## 监控与排错

按 `layer` 聚合以下稳定问题码，并观察比例变化：

- `question_frame`：未确认、无效几何、跨题冲突；
- `blank_config`：缺配置、版本不匹配、空数/答案/分值不一致；
- `alignment`：缺页、低质量、不可逆变换；
- `mapping`：裁切、越界、片段缺失或跨题冲突；
- `recognition`：缺键、重键、多键、未知证据或低置信；
- `grading`：版本冲突、工具与模型冲突、待教师判定。

每个阻断响应都应包含稳定 `code`、问题 `layer` 和可执行 `nextAction`。不要通过提高模型置信阈值或复用旧缓存绕过结构性错误。

## 回退策略

v8 迁移会新增版本表并为旧数据建立 legacy 关联。发生应用问题时，应停止写入并从上线前备份恢复数据库与文件目录，再回退应用；不建议让旧版本应用直接写入已迁移数据库。已创建的新处理版本可保留用于诊断，但不能在旧应用中作为可靠当前结果使用。

## 上线验收最小集合

- 通用 1/2/3/5 空自动化矩阵通过；
- 后端、UI、TypeScript、Ruff、Mypy、构建和 diff 检查通过；
- v7→v8 迁移夹具通过且 foreign key check 为空；
- Chrome/Edge 四档缩放下三层叠加正确；
- 至少一份真实历史答卷完成显式新流程重处理并能切回旧版本；
- 第 8/11 题只在教师核对原页几何后升级为 reviewed oracle。

