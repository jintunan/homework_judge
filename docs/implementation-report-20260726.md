# 作业批改 Agent 第一版实施与验收报告

日期：2026-07-26

## 交付结论

第一版已切换为 Python/FastAPI 单机服务器架构。React 前端由同一 Python 生产进程提供，正式 SQLite 已从 v2 迁移到 v3，旧 Express 运行时、服务端 TypeScript 编译配置及旧后端专用测试已移除。

本次实现支持：

- 初中数学：选择题、填空题、简单简答题；
- 高中物理：选择题、填空题、计算题；
- 上传固定模板试卷与可选参考答案；
- 有参考答案时自动提取题目、答案和评分点；
- 无参考答案时先联网检索，未命中再由模型生成；
- 教师逐题修改、通过、退回、重搜、重生成和最终发布；
- 学生试卷批量上传、模型初评、教师修改分数与批注、整卷确认；
- 保存模型原始响应、解析结果、评分理由、Token 用量、搜索来源、教师最终结果和审计事件；
- 学生报告、班级统计与答案版本追溯。

## 两个原始故障的处理

### `document.destroy is not a function`

服务端 PDF 处理已完全改为 Python `pypdfium2` 与 Pillow，不再执行 Node PDF.js 的 `document.destroy()`。本次 7 页高中物理 PDF 已实际渲染为连续 1–7 页 JPEG，并检查了首尾页。

### `ANSWER_EXTRACTION_INVALID`

真实失败响应并非无效 JSON，而是第 5–8 题评分点合计超过题目满分，旧实现对整卷执行严格 schema 校验后把全部题目一起拒绝。

新实现改为：

1. 容错提取纯 JSON、Markdown 代码块、说明文字包裹 JSON、根数组和常见字段别名；
2. 逐题解析，单个坏节点不会导致整卷失败；
3. 无参考答案模式在最终校验前清空模型提前生成的答案和评分点；
4. 有参考答案模式过滤非法评分点，并用 `Decimal` 等比例缩放超分评分点；
5. 所有清理、归一化和跳过原因保存为诊断记录并展示给教师；
6. 只有零道可用题目时发起一次文本结构修复，禁止递归修复；
7. 原始识别与结构修复分别保存，失败后可安全重试。

真实失败运行 `b87dc2d0-09a6-455b-b214-341970c0103a` 的脱敏回归结果为 15 道题全部进入草稿；参考答案路径中的第 5–8 题完成评分点缩放，题目评分点合计均不超过满分。

## 数据切换证据

正式数据库：

`E:\homework_judge\data\homework-judge.sqlite`

切换前备份：

`E:\homework_judge\data\backups\python-cutover-20260726-1525`

迁移结果：

| 项目 | v2 迁移前 | v3 迁移后 |
|---|---:|---:|
| stored_files | 2 | 2 |
| grading_tasks | 2 | 2 |
| answer_config_versions | 4 | 4 |
| questions | 0 | 0 |
| answer_question_drafts | 15 | 15 |
| answer_resolution_runs | 20 | 20 |
| search_sources | 30 | 30 |
| submissions | 0 | 0 |
| model_runs | 0 | 0 |
| question_reviews | 0 | 0 |
| audit_events | 11 | 11 |

11 张表的主键哈希迁移前后一致，`PRAGMA foreign_key_check` 为空。备份本身也已再次复制迁移并通过相同检查。

## 自动化验收

- `npm run lint`：通过；
- TypeScript：通过；
- mypy：58 个 Python 源文件通过；
- Ruff：通过；
- Vitest：2 个文件、4 个 UI 测试通过；
- pytest：21 个单元/集成测试通过；
- `npm run build`：React 生产构建和 Python `compileall` 通过；
- `npm start`：健康接口、任务 API、React 根页面和前端子路由 fallback 均返回 200；
- 1280×800 Chrome 冒烟：主页和创建任务页无控制台错误；
- `npm run test:model`：只读取模型配置，未发出计费请求；
- 服务停止后 8787、8799、8801 均无遗留监听。

Vite 报告主 JS 包约 839 KB，属于性能优化提示，不阻塞第一版功能。

## 教师仍需完成的验收

以下项目刻意未由自动化代替：

1. 在答案审核页检查现有待审核题目、归一化记录与搜索来源；
2. 修改至少一道标准答案或评分点，确认必须经教师批准才能发布；
3. 上传学生试卷，检查逐题模型识别、评分理由、教师分数和批注保存；
4. 确认学生报告及班级统计只使用教师最终确认结果；
5. 如要执行真实百炼端到端测试，需教师明确同意费用后再发起。当前实施过程没有发送真实计费请求。

## 启动

```powershell
cd E:\homework_judge
npm start
```

访问 `http://127.0.0.1:8787`。

如需回滚，先停止 Python 服务，再按 `README.md` 的回滚步骤恢复上述 v2 备份。禁止 Python 与任何旧后端同时写同一个 SQLite。
