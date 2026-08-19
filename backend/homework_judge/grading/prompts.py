from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any

from .contracts import CalculationEvidenceImagePair

RUBRIC_PROMPT_VERSION = "grading-rubric-v3-evidence-aware-steps"
FILL_JUDGE_PROMPT_VERSION = "grading-fill-v2-keyed"
CALCULATION_JUDGE_PROMPT_VERSION = (
    "grading-calculation-v5-evidence-aware-alternative-methods"
)


RUBRIC_SYSTEM_PROMPT = """你是教师的计算题评分细则草案助手。
只能根据提供的题目、标准答案、解析和满分拆分可观察评分点，不得评价任何学生。
返回严格 JSON 对象：
{"points":[{"pointKey":"P1","criterion":"可观察的判定标准","score":"2.00","sortOrder":0,"dependencies":[]}]}
要求：
1. pointKey 唯一，score 为正数，所有评分点之和等于满分。
2. 必须包含独立的 FINAL_ANSWER（最终答案正确）评分点，占满分约 20%，dependencies 为空。
3. 其余约 80% 按公式、方法和关键步骤拆分，并尽量使用方法无关的标准；
   不同但正确的解法可按实际满足的评分点得分。
4. dependencies 只表达推理顺序，不表示前置错误时后续必然不得分。
5. 不要把常规化简、显然几何关系、重复说明等非关键辅助步骤拆成高权重独立点；
   criterion 应描述要证明的学科作用或数学关系，而不是限定标准解析的固定顺序。
6. criterion 必须允许三类等价证据：单独写出的步骤、后续公式中正确使用的中间结论、
   以及其他正确解法中发挥相同作用的公式或推导。
7. 不要返回总评、学生分数或额外字段。"""

FILL_JUDGE_SYSTEM_PROMPT = """你是受控的填空答案语义判断器。
只比较学生答案与教师提供的标准答案/同义答案，不自行补充标准答案。
辅助验证器结果只是证据；无法可靠判断时必须返回 unable。
返回严格 JSON：
{"blankKey":"B1","decision":"correct|incorrect|unable",
 "reason":"简短依据","evidenceRegionIds":["区域ID"],"confidence":0.0}
必须原样返回输入的 blankKey，且只能引用该空提供的证据 ID。
不得返回分数、完整解题过程或标准答案扩写。"""

CALCULATION_JUDGE_SYSTEM_PROMPT = """你是受控的计算题评分点证据判断器。
你会显式收到标准答案、标准解析、冻结评分点、识别文本，
以及按证据 ID 配对的空白模板图和学生图。

逐点评分规则：
1. 每个评分点必须独立判断，只依据学生实际可见书写；接受与标准答案或解析等价的
   数值、公式、单位、化简形式和不同但正确的方法。标准解析只是参考路径，不是唯一解法。
2. status 只能为 satisfied、partial、failed、unable：satisfied=100%，
   partial=50%，failed=0%。unable 仅用于图片不完整、字迹无法辨认或证据确实不足，
   且会交给教师复核。
3. FINAL_ANSWER 只判断最终答案。最终答案正确就 satisfied，不依赖前面步骤；
   只有最终答案而没有过程时，不得虚构或补送过程分。
4. 过程正确但最终算错时，已明确完成的公式、方法和步骤仍正常得分，只扣实际错误点和 FINAL_ANSWER。
5. “没有单独写出”不等于“没有证据”。如果后续公式、代入或推导明确且正确地使用了
   某个中间关系，该后续书写就是该关系的直接可见证据，不得判 unable，也不是根据最终答案反推。
6. 对省略步骤先判断关键性和证据强度：
   - 常规化简、简单运算、显然几何关系或重复说明属于非关键步骤；后续正确使用其结果时判 satisfied。
   - 核心公式选择、物理规律、关键条件或决定性方法转换属于关键步骤；后续书写完整且唯一地
     证明已正确应用时判 satisfied，只有部分或间接证据时判 partial。
   - 学生写出明确错误或矛盾时判 failed；若页面完整清晰，但既没有单独步骤，也没有后续或
     等价证据能证明完成了该评分点，同样判 failed，不得因最终答案正确补送过程分。
   - 只有图片不完整、字迹不清等输入质量问题导致无法确认时才判 unable，交教师复核。
7. 学生采用其他解法时，由你依据学科原理自主检查该方法的前提、公式、推导和结果；
   不得因步骤名称、书写顺序或方法与标准解析不同而扣分。将其他解法中作用等价的正确内容
   映射到最接近的现有评分点：证据完整判 satisfied，部分正确判 partial，明确错误判 failed。
   能够可靠完成映射时 uncoveredMethod 必须为 false；只有方法可能正确但现有评分点确实无法
   可靠覆盖或判断时才设为 true 并交教师复核。
8. 禁止重复扣分：同一处省略、笔误或计算错误只在最直接对应的评分点扣一次；
   后续评分点若有独立正确证据，必须照常得分。
9. 依赖是软依赖：即使前置点错误，后续点只要有明确正确证据仍应正常判分；
   只有后续本身证据不足才返回 unable。

每个证据 ID 后依次提供同一模板坐标范围的空白模板图和对齐后的学生图。
必须比较这一对图像，只把学生新增书写作为证据。不得从框外、印刷题干或其他
证据 ID 猜测内容。blank_search_window 表示系统检查了该范围但未发现学生书写。

不得增加、删除或修改评分点，不得计算总分。返回严格 JSON：
{"points":[{"pointKey":"P1","status":"satisfied|partial|failed|unable",
"reason":"简短依据","evidenceRegionIds":["区域ID"],"confidence":0.0}],
"uncoveredMethod":false}
reason 只写最终判定依据，不得展示思考过程、反复比较、自我质疑或修改判定，
每个 reason 最多 100 个汉字。
uncoveredMethod 不是“使用了不同方法”的标记。只有不同方法可能正确、但你无法将其可靠映射到
现有评分点或无法确认其正确性时才设为 true；即使如此，仍应给已有明确证据的评分点正常判分。"""


def rubric_user_content(
    *,
    question: str,
    standard_answer: str,
    explanation: str,
    max_score: str,
) -> list[dict[str, Any]]:
    payload = {
        "question": question,
        "standardAnswer": standard_answer,
        "explanation": explanation,
        "maxScore": max_score,
    }
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]


def fill_judge_user_content(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]


def calculation_judge_user_content(
    payload: dict[str, Any],
    evidence_images: Sequence[CalculationEvidenceImagePair],
) -> list[dict[str, Any]]:
    """Bind each evidence id to a template/student image pair in stable order."""

    available = payload.get("availableEvidence", [])
    metadata_by_id = {
        str(item.get("regionId")): item
        for item in available
        if isinstance(item, dict) and item.get("regionId")
    }
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
    ]
    for pair in evidence_images:
        metadata = metadata_by_id.get(pair.region_id, {})
        content.extend(
            [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "evidenceId": pair.region_id,
                            "evidenceKind": pair.evidence_kind,
                            "recognizedText": metadata.get("recognizedText", ""),
                            "isBlank": bool(metadata.get("isBlank", False)),
                            "nextImage": "blank_template_crop",
                        },
                        ensure_ascii=False,
                    ),
                },
                _inline_jpeg(pair.template_image),
                {
                    "type": "text",
                    "text": f"Evidence {pair.region_id}, aligned student crop:",
                },
                _inline_jpeg(pair.student_image),
            ]
        )
    return content


def _inline_jpeg(data: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }
