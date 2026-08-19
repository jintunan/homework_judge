import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type {
  AnnotationPreviewMark,
  GradingEvidence,
  StudentSubmissionDetail
} from "../../shared/contracts";
import { GradingPageOverlay } from "../../client/src/features/grading/GradingPageOverlay";
import {
  FillBlankConfigReviewNotice,
  FillBlankReviewCards,
  questionScoreText
} from "../../client/src/features/grading/GradingWorkspacePage";
import { ApiError } from "../../client/src/lib/api";

const page: StudentSubmissionDetail["pages"][number] = {
  id: "page",
  pageNumber: 1,
  width: 1000,
  height: 1400,
  templatePageId: "template",
  templatePageNumber: 1,
  imageUrl: "/api/student-pages/page",
  alignment: {
    direction: "student_original_to_template",
    transform: null,
    quality: 0.98,
    method: "feature_homography",
    status: "aligned"
  }
};

const evidence: GradingEvidence[] = [{
  page_id: "page",
  region_id: "region",
  original_bbox: {x: 100, y: 200, width: 300, height: 100},
  cropped_image_path: null,
  recognized_text: "AC",
  char_or_step_range: null
}];

const marks: AnnotationPreviewMark[] = [
  {
    mark_type: "error_circle",
    page_id: "page",
    question_result_id: "result",
    question_id: "question",
    box: {x: 92, y: 192, width: 316, height: 116},
    target_box: {x: 100, y: 200, width: 300, height: 100},
    label: "错误位置",
    color: "#DC2626"
  },
  {
    mark_type: "partial_score",
    page_id: "page",
    question_result_id: "result",
    question_id: "question",
    box: {x: 450, y: 200, width: 80, height: 80},
    target_box: null,
    label: "4.00/6.00",
    color: "#F59E0B"
  }
];

describe("grading score display", () => {
  it("labels unresolved placeholder scores instead of displaying zero", () => {
    expect(questionScoreText({finalScore: null, maxScore: "10.00"})).toBe(
      "待复核/10.00"
    );
    expect(questionScoreText({finalScore: "4.80", maxScore: "10.00"})).toBe(
      "4.80/10.00"
    );
  });
});

describe("grading page overlay", () => {
  it("shows evidence, an error circle and a partial-score marker", () => {
    const {container} = render(
      <GradingPageOverlay page={page} marks={marks} evidence={evidence} showEvidence showMarks />
    );
    expect(screen.getByLabelText("批改标记覆盖层")).toHaveAttribute("viewBox", "0 0 1000 1400");
    expect(container.querySelectorAll(".grading-evidence-box")).toHaveLength(1);
    expect(container.querySelectorAll("ellipse")).toHaveLength(1);
    expect(container.querySelectorAll("polygon")).toHaveLength(1);
    expect(container.querySelectorAll(".grading-mark line")).toHaveLength(0);
    expect(screen.getByText("4.00/6.00")).toBeInTheDocument();
  });

  it("can hide final marks without hiding the original paper", () => {
    const {container} = render(
      <GradingPageOverlay page={page} marks={marks} evidence={evidence} showEvidence={false} showMarks={false} />
    );
    expect(container.querySelector("image")).toBeInTheDocument();
    expect(container.querySelector("ellipse")).not.toBeInTheDocument();
    expect(container.querySelector(".grading-evidence-box")).not.toBeInTheDocument();
  });

  it("shows the confirmed complete frame by default and preserves every backend coordinate", () => {
    const exactFrame = [
      {x: 83, y: 121},
      {x: 917, y: 132},
      {x: 889, y: 1168},
      {x: 71, y: 1141}
    ];
    const exactEvidence = [
      {x: 211, y: 302},
      {x: 409, y: 298},
      {x: 417, y: 357},
      {x: 205, y: 361}
    ];
    const {container} = render(
      <GradingPageOverlay
        page={page}
        marks={[]}
        evidence={[{...evidence[0], original_polygon: exactEvidence}]}
        questionFrames={[{
          id: "frame-region",
          questionId: "question",
          pageId: "page",
          polygon: exactFrame
        }]}
        blankAnchors={[]}
        showEvidence={false}
        showBlankAnchors={false}
        showMarks={false}
      />
    );

    expect(container.querySelector(".grading-question-frame")).toHaveAttribute(
      "points",
      "83,121 917,132 889,1168 71,1141"
    );
    expect(container.querySelector(".grading-recognition-evidence")).not.toBeInTheDocument();
  });

  it("switches complete frames, blank anchors and recognition evidence independently", () => {
    const layerProps = {
      page,
      marks: [],
      questionFrames: [{
        id: "frame-region",
        questionId: "question",
        pageId: "page",
        polygon: [{x: 80, y: 120}, {x: 920, y: 120}, {x: 900, y: 1180}, {x: 70, y: 1160}]
      }],
      blankAnchors: [{
        blankKey: "B1",
        pageId: "page",
        coordinateSpace: "student_page_pixel" as const,
        studentPolygon: [{x: 205, y: 290}, {x: 425, y: 290}, {x: 425, y: 350}, {x: 205, y: 350}],
        studentBBox: {x: 205, y: 290, width: 220, height: 60}
      }],
      evidence: [{
        ...evidence[0],
        original_polygon: [{x: 200, y: 285}, {x: 430, y: 285}, {x: 430, y: 355}, {x: 200, y: 355}]
      }],
      showMarks: false
    };
    const {container, rerender} = render(
      <GradingPageOverlay {...layerProps} showQuestionFrames showBlankAnchors={false} showEvidence={false} />
    );

    expect(container.querySelectorAll(".grading-question-frame")).toHaveLength(1);
    expect(container.querySelectorAll(".grading-blank-anchor")).toHaveLength(0);
    expect(container.querySelectorAll(".grading-recognition-evidence")).toHaveLength(0);

    rerender(<GradingPageOverlay {...layerProps} showQuestionFrames={false} showBlankAnchors showEvidence={false} />);
    expect(container.querySelectorAll(".grading-question-frame")).toHaveLength(0);
    expect(container.querySelector(".grading-blank-anchor")).toHaveAttribute(
      "points",
      "205,290 425,290 425,350 205,350"
    );
    expect(container.querySelectorAll(".grading-recognition-evidence")).toHaveLength(0);

    rerender(<GradingPageOverlay {...layerProps} showQuestionFrames={false} showBlankAnchors={false} showEvidence />);
    expect(container.querySelectorAll(".grading-question-frame")).toHaveLength(0);
    expect(container.querySelectorAll(".grading-blank-anchor")).toHaveLength(0);
    expect(container.querySelector(".grading-recognition-evidence")).toHaveAttribute(
      "points",
      "200,285 430,285 430,355 200,355"
    );
  });

  it("safely hides an anchor that has no mapped student polygon", () => {
    const {container} = render(
      <GradingPageOverlay
        page={page}
        marks={[]}
        evidence={[]}
        blankAnchors={[{
          blankKey: "B1",
          pageId: "page",
          coordinateSpace: "student_page_pixel",
          studentPolygon: null,
          studentBBox: {x: 205, y: 290, width: 220, height: 60}
        }]}
        showEvidence={false}
        showBlankAnchors
        showMarks={false}
      />
    );

    expect(container.querySelector(".grading-blank-anchor")).not.toBeInTheDocument();
    expect(container.querySelector("rect.grading-blank-anchor")).not.toBeInTheDocument();
  });
});

function blankResult(blankKey: string, index: number) {
  return {
    id: `blank-${blankKey}`,
    blankKey,
    studentAnswer: `学生答案-${blankKey}`,
    standardAnswers: [`标准答案-${blankKey}`, `同义答案-${blankKey}`],
    status: index % 2 === 0 ? "correct" as const : "needs_review" as const,
    score: index % 2 === 0 ? "1.00" : "0.00",
    maxScore: "1.00",
    reviewReasons: index % 2 === 0 ? [] : ["LOW_RECOGNITION_CONFIDENCE"],
    evidenceRefs: [],
    frameSetId: "frame-v3",
    blankConfigVersionId: "blank-config-v5",
    processingRevisionId: "processing-v7",
    gradingRevision: 9
  };
}

describe.each([1, 2, 3, 5])("fill-blank review cards with %i blanks", (blankCount) => {
  it("renders every dynamic blankKey with answer, decision, score, versions and correction controls", () => {
    const onCorrect = vi.fn();
    const blankResults = Array.from({length: blankCount}, (_, index) => blankResult(`B${index + 1}`, index));
    render(
      <FillBlankReviewCards
        questionResultId="result"
        gradingRevision={9}
        blankResults={blankResults}
        teacherReason="逐空核对"
        savingBlankKey={null}
        onCorrect={onCorrect}
      />
    );

    for (const blank of blankResults) {
      const card = screen.getByTestId(`blank-review-${blank.blankKey}`);
      expect(card).toHaveTextContent(blank.blankKey);
      expect(card).toHaveTextContent(blank.studentAnswer);
      expect(card).toHaveTextContent("标准答案");
      expect(card).toHaveTextContent(blank.standardAnswers.join(" / "));
      expect(card).toHaveTextContent(`${blank.score} / ${blank.maxScore}`);
      expect(card).toHaveTextContent("frame-v3");
      expect(card).toHaveTextContent("blank-config-v5");
      expect(card).toHaveTextContent("processing-v7");
      expect(card).toHaveTextContent("评分 R9");
      if (blank.status === "needs_review") {
        expect(card).toHaveTextContent("LOW_RECOGNITION_CONFIDENCE");
      }
      expect(screen.getByLabelText(`${blank.blankKey} 学生答案修正`)).toBeInTheDocument();
      expect(screen.getByLabelText(`${blank.blankKey} 最终判定`)).toBeInTheDocument();
    }

    const selected = blankResults.at(-1)!;
    fireEvent.change(screen.getByLabelText(`${selected.blankKey} 学生答案修正`), {
      target: {value: "教师修正值"}
    });
    fireEvent.click(screen.getByRole("button", {name: `${selected.blankKey} 按修正答案重判`}));
    expect(onCorrect).toHaveBeenCalledWith(selected.blankKey, {recognizedText: "教师修正值"});
  });
});

describe("legacy fill-blank review cards", () => {
  it("warns safely and disables correction when required versions are missing", () => {
    const legacy = {
      ...blankResult("B1", 0),
      frameSetId: null,
      blankConfigVersionId: null,
      processingRevisionId: null,
      gradingRevision: null
    };
    render(
      <FillBlankReviewCards
        questionResultId="legacy-result"
        gradingRevision={null}
        blankResults={[legacy]}
        teacherReason="核对旧结果"
        savingBlankKey={null}
        onCorrect={vi.fn()}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent("版本信息不完整");
    expect(screen.getByRole("button", {name: "B1 按修正答案重判"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "B1 覆盖最终判定"})).toBeDisabled();
  });
});

describe("fill-blank configuration start error", () => {
  it("links the teacher to the review page and lists every blocked question", () => {
    const error = new ApiError(
      "FILL_BLANK_CONFIG_REVIEW_REQUIRED",
      "填空题配置需要检查",
      {
        questions: [
          {questionId: "q9", questionNumber: "9", reasonCodes: ["answer_split_ambiguous"]},
          {questionId: "q11", questionNumber: "11", reasonCodes: ["blank_standard_answer_missing"]}
        ]
      }
    );

    render(
      <MemoryRouter>
        <FillBlankConfigReviewNotice error={error} taskId="task-1" />
      </MemoryRouter>
    );

    expect(screen.getByRole("alert")).toHaveTextContent("请先逐空检查并保存填空题配置");
    expect(screen.getByRole("alert")).toHaveTextContent("第 9、11 题");
    expect(screen.getByRole("link", {name: "返回题目复核页"})).toHaveAttribute(
      "href",
      "/tasks/task-1/review"
    );
  });

  it("does not render the review notice for unrelated errors", () => {
    const error = new ApiError("NETWORK_ERROR", "网络异常");
    const {container} = render(
      <MemoryRouter>
        <FillBlankConfigReviewNotice error={error} taskId="task-1" />
      </MemoryRouter>
    );

    expect(container).toBeEmptyDOMElement();
  });
});
