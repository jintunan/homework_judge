from __future__ import annotations

from typing import Any

from ..db.database import Database
from ..db.repositories.reviews import get_submission_review
from ..db.repositories.submissions import get_progress, list_submissions
from ..db.repositories.tasks import get_answer_version, get_task_summary, list_questions


async def build_student_report(
    database: Database,
    submission_id: str,
) -> dict[str, Any]:
    review = await get_submission_review(database, submission_id)
    max_score = sum(float(question["maxScore"]) for question in review["reviews"])
    return {
        "submission": review["submission"],
        "task": review["task"],
        "answerVersion": review["answerVersion"],
        "isFinal": review["submission"]["status"] == "confirmed",
        "totalScore": (
            review["submission"]["finalTotalScore"]
            if review["submission"]["status"] == "confirmed"
            else None
        ),
        "maxScore": max_score,
        "reviews": review["reviews"],
    }


async def build_class_statistics(
    database: Database,
    task_id: str,
) -> dict[str, Any]:
    task = await get_task_summary(database, task_id)
    submissions = await list_submissions(database, task_id)
    confirmed = [
        submission for submission in submissions if submission["status"] == "confirmed"
    ]
    active_id = (
        task["activeAnswerVersion"]["id"] if task["activeAnswerVersion"] else ""
    )
    version_rows = await database.fetch_all(
        """
        SELECT v.id, v.version_number, COUNT(s.id) AS submission_count,
               SUM(CASE WHEN s.status = 'confirmed' THEN 1 ELSE 0 END)
                 AS confirmed_count
        FROM answer_config_versions v
        LEFT JOIN submissions s ON s.answer_version_id = v.id
        WHERE v.task_id = ?
        GROUP BY v.id
        HAVING COUNT(s.id) > 0 OR v.id = ?
        ORDER BY v.version_number
        """,
        (task_id, active_id),
    )
    version_numbers = {
        str(row["id"]): int(row["version_number"]) for row in version_rows
    }
    questions: list[dict[str, Any]] = []
    max_score_by_version: dict[str, float] = {}
    for version in version_rows:
        version_id = str(version["id"])
        version_questions = await list_questions(database, task_id, version_id)
        max_score_by_version[version_id] = sum(
            float(question["maxScore"]) for question in version_questions
        )
        questions.extend(
            {
                **question,
                "answerVersionNumber": int(version["version_number"]),
            }
            for question in version_questions
        )

    confirmed_scores = [
        (submission, float(submission["finalTotalScore"]))
        for submission in confirmed
        if submission["finalTotalScore"] is not None
    ]
    scores = [score for _submission, score in confirmed_scores]
    total_score = float(task["totalScore"])
    average_score = sum(scores) / len(scores) if scores else None
    band_definitions: list[dict[str, Any]] = [
        {"label": "优秀 · 90%+", "minPercent": 0.9},
        {"label": "良好 · 80–89%", "minPercent": 0.8},
        {"label": "中等 · 70–79%", "minPercent": 0.7},
        {"label": "及格 · 60–69%", "minPercent": 0.6},
        {"label": "待提升 · <60%", "minPercent": 0.0},
    ]
    score_bands: list[dict[str, Any]] = []
    for index, band in enumerate(band_definitions):
        upper = (
            float("inf")
            if index == 0
            else float(band_definitions[index - 1]["minPercent"])
        )
        count = 0
        for submission, score in confirmed_scores:
            version_id = submission["answerVersionId"]
            version_total = max_score_by_version.get(str(version_id), total_score)
            ratio = score / version_total if version_total > 0 else 0
            if float(band["minPercent"]) <= ratio < upper:
                count += 1
        score_bands.append({**band, "count": count})

    question_stats: list[dict[str, Any]] = []
    for question in questions:
        row = await database.fetch_one(
            """
            SELECT COALESCE(SUM(r.final_score), 0) AS total,
                   COUNT(r.id) AS count
            FROM question_reviews r
            JOIN submissions s ON s.id = r.submission_id
            WHERE r.question_id = ? AND s.status = 'confirmed'
            """,
            (question["id"],),
        )
        count = int(row["count"] if row else 0)
        average = float(row["total"] if row else 0) / count if count else 0
        max_score = float(question["maxScore"])
        question_stats.append(
            {
                "questionId": question["id"],
                "answerVersionId": question["answerVersionId"] or "",
                "answerVersionNumber": question["answerVersionNumber"],
                "number": question["number"],
                "averageScore": average,
                "maxScore": max_score,
                "scoreRate": average / max_score if max_score > 0 else 0,
            }
        )

    students: list[dict[str, Any]] = []
    for submission in submissions:
        version_id = submission["answerVersionId"]
        version_number = version_numbers.get(str(version_id)) if version_id else None
        if version_id and version_number is None:
            version_number = int(
                (await get_answer_version(database, str(version_id)))["versionNumber"]
            )
        students.append(
            {
                "submissionId": submission["id"],
                "studentName": submission["studentName"],
                "status": submission["status"],
                "score": (
                    submission["finalTotalScore"]
                    if submission["status"] == "confirmed"
                    else None
                ),
                "confirmedAt": submission["confirmedAt"],
                "answerVersionId": version_id,
                "answerVersionNumber": version_number,
            }
        )
    return {
        "subject": task["subject"],
        "activeAnswerVersion": task["activeAnswerVersion"],
        "answerVersions": [
            {
                "id": row["id"],
                "versionNumber": int(row["version_number"]),
                "submissionCount": int(row["submission_count"]),
                "confirmedCount": int(row["confirmed_count"] or 0),
            }
            for row in version_rows
        ],
        "progress": await get_progress(database, task_id),
        "confirmedCount": len(confirmed),
        "averageScore": average_score,
        "highestScore": max(scores) if scores else None,
        "lowestScore": min(scores) if scores else None,
        "totalScore": total_score,
        "scoreBands": score_bands,
        "questions": question_stats,
        "students": students,
    }
