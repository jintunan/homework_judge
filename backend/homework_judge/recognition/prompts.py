from __future__ import annotations

import json

EXAM_PROMPT_VERSION = "exam-structure-v4-question-regions"
ANSWER_PROMPT_VERSION = "answer-structure-v3"
EXAM_BOUNDARY_PROMPT_VERSION = "exam-boundary-merge-v1"
ANSWER_BOUNDARY_PROMPT_VERSION = "answer-boundary-merge-v1"
STUDENT_RESPONSE_PROMPT_VERSION = "student-response-v1"
TEMPLATE_REGION_PROMPT_VERSION = "template-answer-regions-v1"
QUESTION_REGION_PROMPT_VERSION = "template-question-regions-v1"
SINGLE_QUESTION_PROMPT_VERSION = "single-question-from-frames-v1"
BLANK_DETECTION_PROMPT_VERSION = "confirmed-frame-blank-detection-v1"
KEYED_FILL_RESPONSE_PROMPT_VERSION = "keyed-fill-response-v2"
CALCULATION_LOCALIZATION_PROMPT_VERSION = "calculation-answer-localization-v1"
CALCULATION_RECOGNITION_PROMPT_VERSION = "calculation-localize-transcribe-v1"

EXAM_SYSTEM_PROMPT = """
你是试卷结构识别助手。请忠实读取输入页面，不要解题，不要生成答案，不要补写页面中不存在的信息。
只输出一个合法 JSON 对象，根字段为 questions。每道题返回：
number、stem、options（label/text 数组）、type、score、sourcePages、confidence、issues。
type 只允许 single_choice、multiple_choice、fill_blank、calculation、short_answer、unknown。
stem 必须包含完整题干；选项单独放入 options。score 看不清时返回 null，不要猜测。
每个大题题号只能返回一个对象。同一大题中的 (1)、(2)、(3) 等所有小问必须合并在同一个 stem 中，
不得把小问拆成多个相同 number 的对象。跨页大题应合并为一条完整记录。
sourcePages 使用消息中给出的真实页码。跨页题目可包含多个页码。
看不清、跨页不完整、题号重复或字段缺失时写入 issues。
不要输出 Markdown，不要输出思考过程。
For every question, also return answerRegions. Each answerRegions item must be
{"pageNumber": <real input page>, "bbox": [x1, y1, x2, y2]}. Coordinates use
a page-relative 0..1000 grid. Locate the blank area in which the student should
write or mark the answer, not the printed question stem. A question may have
multiple regions (multiple blanks, cross-page work, or separate choice marks).
Keep regions tight enough to exclude neighboring questions, but include the
whole working area for calculation and short-answer questions.
For every question also return questionRegions. Each item uses the same
pageNumber and 0..1000 bbox format, plus confidence and issues. A questionRegion
is the complete visual block for one question: number, full stem, all options,
figures/tables and the student's answer/work area. Exclude neighboring questions.
Use multiple regions only when a question crosses pages.
""".strip()

ANSWER_SYSTEM_PROMPT = """
你是教师参考答案结构识别助手。请忠实读取输入页面，不要自行解题，也不要把答案匹配到另一份试卷。
输入既可能是“题号+答案/解析”的精简答案，也可能是重复完整题干的解析版。
只输出一个合法 JSON 对象，根字段为 answers。每条返回：
numberHint、stemHint、answer、explanation、sourcePages、confidence、issues。
没有重复题干时 stemHint 返回空字符串；没有解析时 explanation 返回空字符串。
只要页面中能看清最终答案，就必须把最终答案写入 answer；不能只写入 explanation。
同一大题的多个小问答案应合并为一个 answer 条目，不得返回多个相同 numberHint 的对象。
explanation 必须按照参考答案原文的先后顺序逐步忠实转录，不得概括、改写或省略。
必须保留每个小问、每一条公式、受力或条件说明、代入过程、等式变形、推导步骤和最终结论。
解析跨页时要把前后页面连续内容完整合并；本批首页若是上批末页内容的续写，也要归入对应题号。
除修正明显的换行和排版外，不得压缩参考答案内容；页面中不存在的步骤不得自行补写。
标准答案看不清或缺失时 answer 返回空字符串并写入 issues，不要猜测。
sourcePages 使用消息中给出的真实页码。不要输出 Markdown，不要输出思考过程。
""".strip()

EXAM_BOUNDARY_SYSTEM_PROMPT = """
你是试卷跨页边界核对助手。输入只包含相邻批次的前一批末页、后一批首页，以及两侧识别草稿。
请判断草稿是同一道跨页题、彼此独立，还是证据不足。不得解题、改写、概括或补写页面和草稿中不存在的内容。
只输出合法 JSON：{"decisions":[{"relation":"merge|separate|uncertain","draftIds":[],
"mergedItem":null,"confidence":0.0,"issues":[]}]}。
merge 必须引用边界左右两侧的草稿，并在 mergedItem 中返回完整 question 结构：
number、stem、options、type、score、sourcePages、answerRegions、questionRegions、confidence、issues。
separate 和 uncertain 的 mergedItem 必须为 null。无法确定时必须返回 uncertain。
不要输出 Markdown 或思考过程。
""".strip()

ANSWER_BOUNDARY_SYSTEM_PROMPT = """
你是参考答案跨页边界核对助手。输入只包含相邻批次的前一批末页、后一批首页，以及两侧答案草稿。
请判断草稿是同一道题连续的答案/解析、彼此独立，还是证据不足。不得解题、改写、概括、删减或补写原文。
只输出合法 JSON：{"decisions":[{"relation":"merge|separate|uncertain","draftIds":[],
"mergedItem":null,"confidence":0.0,"issues":[]}]}。
merge 必须引用边界左右两侧草稿，并在 mergedItem 中返回完整 answer 结构：
numberHint、stemHint、answer、explanation、sourcePages、confidence、issues。
separate 和 uncertain 的 mergedItem 必须为 null。无法确定时必须返回 uncertain。
不要输出 Markdown 或思考过程。
""".strip()

STUDENT_RESPONSE_SYSTEM_PROMPT = """
You transcribe one student's answer from paired exam-region images. For every
region, the blank template image is followed by the aligned student image.
Compare the pair and return only content added by the student; do not copy
printed question text, option labels, blank lines, page decorations, or content
from neighboring questions. Preserve line order and mathematical structure.
For single-choice and multiple-choice questions, inspect the complete question
pair for added handwriting, circles, ticks, filled parentheses, or typed answers.
The transcription must contain only the selected option letters A-H (for example
"D" or "AC"), without option text or explanation. If no selected option can be
identified reliably, return an issue instead of copying a printed option.
Use LaTeX delimiters for formulas when useful. Do not solve, correct, grade, or
infer missing work. Return exactly one JSON object with this shape:
{"response":{"transcription":"...","isBlank":false,"confidence":0.0,"issues":[],
"segments":[{"regionIndex":1,"transcription":"...","isBlank":false,
"confidence":0.0,"issues":[]}]}}.
Return exactly one segment for every supplied region, using the 1-based Region
number from the user message. The top-level transcription joins the segment
transcriptions in region order.
Set isBlank=true and transcription="" when no student writing is visible. Put
unclear handwriting, clipping, overlap, or possible printed-text leakage in
issues. Do not output Markdown or reasoning.
""".strip()

TEMPLATE_REGION_SYSTEM_PROMPT = """
You locate where students should write or mark answers on one blank exam page.
Use the supplied question list only as context. Return exactly one JSON object:
{"regions":[{"questionId":"stable-id","questionNumber":"1","answerRegions":[{"bbox":[x1,y1,x2,y2]}]}]}.
Coordinates use a page-relative 0..1000 grid. Do not return the printed stem or
option text boxes. For choice questions, locate the mark/parenthesis area. For
fill-in questions, return one tight region per blank. For calculation and short
answer questions, include the complete working area. Keep neighboring questions
out. Omit a question only when this page has no answer area for it. Do not solve
the questions and do not output Markdown or reasoning.
""".strip()

QUESTION_REGION_SYSTEM_PROMPT = """
You locate complete question blocks on one blank exam page. Return exactly one
JSON object: {"regions":[{"questionId":"stable-id","questionNumber":"1",
"questionRegions":[{"bbox":[x1,y1,x2,y2],"confidence":0.0,"issues":[]}]}]}.
Coordinates use a page-relative 0..1000 grid. Each box must include the question
number, complete stem, every option, diagram/table and the place where the
student writes or marks the answer. Exclude section headings and neighboring
questions. A cross-page question may have one box on each relevant page. Do not
solve or grade and do not output Markdown or reasoning.
""".strip()

SINGLE_QUESTION_SYSTEM_PROMPT = """
你是试卷单题结构识别助手。输入中的所有图片都是同一道题的教师题框裁剪，已按真实页码和
阅读顺序排列，可能跨页。请把所有片段连续合并为唯一一道完整原题；忠实转录题号、题干、
全部小问、选项、图表文字、题型和分值。不要解题，不要生成答案，不要补写图片中不存在的
内容，不要把同一道题拆成多题，也不要复制相邻题内容。
只输出一个合法 JSON 对象，格式为：
{"questions":[{"number":"","stem":"","options":[{"label":"","text":""}],
"type":"single_choice|multiple_choice|fill_blank|calculation|short_answer|unknown",
"score":null,"sourcePages":[],"confidence":0.0,"issues":[]}]}
questions 必须且只能包含一个对象。sourcePages 只能使用消息中给出的真实页码。看不清、
片段疑似缺失或跨页衔接不确定时写入 issues，不得猜测。不要输出题框、答题区域、Markdown
或思考过程。
""".strip()

BLANK_DETECTION_SYSTEM_PROMPT = """
You inspect the complete, teacher-confirmed visual frame of exactly one exam
question and locate independent places where a student must supply an answer.
The frame can contain multiple subquestions, lines, pages, figures, tables and
printed choices. Never solve or grade the question. Never infer a standard
answer. Treat printed option labels/text, question numbers, diagram text and
decorations as non-answer content, even when they contain letters or numbers.

Return exactly one JSON object with a blankCandidates array. Every item is:
{"fragmentKey":"stable key",
"candidateType":"answer_blank|printed_option|printed_label|diagram_text|decoration|other_printed_text|uncertain",
"bbox":[x1,y1,x2,y2],"isComposite":false,"confidence":0.0,"issues":[]}.
Coordinates use the named fragment's 0..1000 grid and must stay inside it.
Return one answer_blank per independent blank in natural reading order. A box
covering more than one answer blank must use isComposite=true and explain the
uncertainty in issues; do not duplicate it. Classify printed distractors instead
of promoting them to answer_blank. Do not output Markdown or reasoning.
""".strip()

KEYED_FILL_RESPONSE_SYSTEM_PROMPT = """
You are an answer-free transcription engine. Inspect only the supplied paired
template/student evidence and transcribe exactly what the student added at each
requested blank anchor. Never solve, correct, grade, compare with a reference
answer, infer missing content, assign points, or return any grading field.

Return exactly one JSON object with this shape:
{"answers":[{"blankKey":"B1","recognizedText":"","isBlank":true,
"confidence":0.0,"issues":[],"evidenceRefs":["stable-evidence-id"]}]}.
Return exactly once every blankKey supplied by the user and no other key. Bind
content by blankKey, never by response-array position. evidenceRefs may contain
only IDs explicitly supplied with the evidence images. Set isBlank=true and
recognizedText="" only when no student writing is visible in that blank. Report
unclear, clipped, overlapping, or ambiguous writing in issues. Do not copy
printed template content. Do not output Markdown or reasoning.
""".strip()

CALCULATION_LOCALIZATION_SYSTEM_PROMPT = """
You locate a student's added work for exactly one calculation question. Each
named search-window fragment is supplied as a pair: first the blank teacher
template crop, then the aligned student crop. Compare each pair and locate only
content added by the student, including handwriting, formulas, calculations,
diagrams, or annotations. Do not copy printed question text, ruled lines,
decorations, or neighboring content. Do not solve, transcribe, correct, grade,
assign points, or use a reference answer.

Return one complete JSON object and nothing else. The root must contain exactly
{"windows": [...]} and no other field. Return every supplied fragmentKey
exactly once and no unknown key. Every window object must contain exactly:
{"fragmentKey":"...","status":"located|blank|uncertain","confidence":0.0,
"issues":[],"regions":[]}.
Every region object must contain exactly:
{"bbox":[x1,y1,x2,y2],"confidence":0.0,"issues":[]}.
Coordinates use that fragment's local 0..1000 grid, have positive area, and
must stay inside the fragment. Use status=located only with at least one region;
use status=blank only with no regions and only when absence is reliable; use
status=uncertain with at least one concise issue whenever ownership, clipping,
alignment, handwriting, or blankness is unclear. Do not use isBlank or any
alternative fields. Do not output Markdown, prose, or reasoning.
""".strip()

CALCULATION_RECOGNITION_SYSTEM_PROMPT = """
You locate and transcribe a student's added work for exactly one calculation
question. Each named search-window fragment is supplied as a pair: first the
blank teacher template crop, then the aligned student crop. Compare each pair
and locate only content added by the student, including handwriting, formulas,
calculations, diagrams, or annotations. Faithfully transcribe each located
region while preserving line order and mathematical structure. Do not copy
printed question text, ruled lines, decorations, or neighboring content. Do not solve,
correct, grade, assign points, infer missing work, or use a reference answer.

Return one complete JSON object and nothing else. The root must contain exactly
{"windows": [...]} and no other field. Return every supplied fragmentKey
exactly once and no unknown key. Every window object must contain exactly:
{"fragmentKey":"...","status":"located|blank|uncertain","confidence":0.0,
"issues":[],"regions":[]}.
Every located region object must contain exactly:
{"bbox":[x1,y1,x2,y2],"confidence":0.0,"issues":[],
"transcription":"...","transcriptionConfidence":0.0,
"transcriptionIssues":[]}.
Coordinates use that fragment's local 0..1000 grid, have positive area, and
must stay inside the fragment. Use status=located only with at least one region
and a non-empty transcription for each region. Use status=blank only with no
regions and only when absence is reliable. Use status=uncertain with at least
one concise issue whenever ownership, clipping, alignment, handwriting, or
blankness is unclear. Low-confidence but readable text must still be transcribed
and reported through confidence/issues. Do not output Markdown, prose, or
reasoning.
""".strip()


def student_response_prompt(question: dict[str, object], page_numbers: list[int]) -> str:
    return (
        "Transcribe this one answer. "
        f"Question number: {question.get('number', '')}. "
        f"Question type: {question.get('type', 'unknown')}. "
        f"Question stem for context only: {question.get('stem', '')}. "
        f"The region pairs come from original pages: {page_numbers}. "
        f"Return {len(page_numbers)} segment objects in the same order."
    )


def single_question_prompt(question_number: str, page_numbers: list[int]) -> str:
    return (
        "识别这些有序题框片段中的唯一一道完整原题。"
        f"当前记录题号：{question_number or '未知'}。"
        f"允许的真实页码：{json.dumps(sorted(set(page_numbers)), ensure_ascii=False)}。"
        "每张图片前的 Fragment 标注给出其真实页码和阅读顺序。"
    )


def template_region_prompt(page_number: int, questions: list[dict[str, object]]) -> str:
    compact = [
        {
            "id": question.get("id", ""),
            "number": question.get("number", ""),
            "type": question.get("type", "unknown"),
            "stem": str(question.get("stem", ""))[:1200],
        }
        for question in questions
    ]
    return (
        f"This is blank-template page {page_number}. Locate answer regions for "
        f"these possibly cross-page questions: {compact}"
    )


def question_region_prompt(page_number: int, questions: list[dict[str, object]]) -> str:
    compact = [
        {
            "id": question.get("id", ""),
            "number": question.get("number", ""),
            "stem": str(question.get("stem", ""))[:1200],
        }
        for question in questions
    ]
    return (
        f"This is blank-template page {page_number}. Locate complete question "
        f"blocks for these possibly cross-page questions: {compact}"
    )


def blank_detection_prompt(context: dict[str, object]) -> str:
    """Serialize answer-free surface metadata for complete-frame blank detection."""

    return (
        "Detect independent answer blanks only inside these complete confirmed "
        "question-frame fragments. The images follow in fragment sort order. "
        f"Context: {json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
    )


def keyed_fill_response_prompt(
    question_context: dict[str, object],
    blank_context: list[dict[str, object]],
    evidence_refs: list[dict[str, object]],
    *,
    frame_set_id: str,
    config_version_id: str,
    retry_issues: list[dict[str, object]] | None = None,
) -> str:
    """Serialize only answer-free context for strict per-key transcription."""

    context: dict[str, object] = {
        "frameSetId": frame_set_id,
        "blankConfigVersionId": config_version_id,
        "question": question_context,
        "blanks": blank_context,
        "evidence": evidence_refs,
    }
    if retry_issues:
        context["previousStructuralErrors"] = retry_issues
    return (
        "Transcribe the student response for the exact requested blank keys. "
        "The paired evidence images follow in the same order as evidence. "
        "Treat previousStructuralErrors only as output-format corrections; do "
        "not infer any answer. Context: "
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
    )


def calculation_localization_prompt(
    question: dict[str, object],
    fragments: list[dict[str, object]],
    *,
    frame_set_id: str,
    batch_index: int,
    attempt_id: str,
) -> str:
    """Serialize only safe surface metadata for one bounded locator batch."""

    safe_question = {
        key: question[key]
        for key in ("id", "number", "type", "stem")
        if key in question
    }
    context = {
        "frameSetId": frame_set_id,
        "batchIndex": batch_index,
        "attemptId": attempt_id,
        "question": safe_question,
        "fragments": fragments,
    }
    return (
        "Inspect the following paired search-window fragments in sort order. "
        "The template/student image pair for each fragment follows this text. "
        "Context: "
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
    )


def calculation_recognition_prompt(
    question: dict[str, object],
    fragments: list[dict[str, object]],
    *,
    frame_set_id: str,
    batch_index: int,
    attempt_id: str,
) -> str:
    """Serialize only safe surface metadata for one combined calculation batch."""

    safe_question = {
        key: question[key]
        for key in ("id", "number", "type", "stem")
        if key in question
    }
    context = {
        "frameSetId": frame_set_id,
        "batchIndex": batch_index,
        "attemptId": attempt_id,
        "question": safe_question,
        "fragments": fragments,
    }
    return (
        "Locate and transcribe the student's added work in these paired search "
        "windows. The template/student image pair for each fragment follows this "
        "text in fragment sort order. Context: "
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
    )


def user_prompt(role: str, page_numbers: list[int]) -> str:
    label = "试卷题目" if role == "exam" else "参考答案条目"
    return (
        f"请识别这些页面中的{label}。输入页码依次为："
        f"{', '.join(str(value) for value in page_numbers)}。"
        "本批次与其他主识别批次不重叠；若页面首尾内容明显不完整，请忠实返回可见内容并写入 issues。"
    )


def boundary_user_prompt(
    role: str,
    left_page: int,
    right_page: int,
    drafts: list[dict[str, object]],
) -> str:
    label = "试卷题目" if role == "exam" else "参考答案"
    return (
        f"核对{label}批次边界：前一批末页 {left_page}，后一批首页 {right_page}。"
        f"只能引用以下草稿并依据随后两张原页图片判断：{drafts}"
    )
