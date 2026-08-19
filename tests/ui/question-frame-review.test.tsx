import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterAll, beforeAll, describe, expect, it, vi} from "vitest";
import type {QuestionFrameFragment, QuestionFrameItem} from "../../shared/contracts";
import {
  TemplateQuestionFrameEditor,
  type TemplateQuestionFrameEditorProps,
  type TemplateQuestionFramePage
} from "../../client/src/features/review/TemplateQuestionFrameEditor";

class TestPointerEvent extends MouseEvent {
  readonly pointerId: number;

  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 0;
  }
}

beforeAll(() => vi.stubGlobal("PointerEvent", TestPointerEvent));
afterAll(() => vi.unstubAllGlobals());

const pages: TemplateQuestionFramePage[] = [
  {id: "template-page-two", pageNumber: 2, width: 1000, height: 2000, imageUrl: "/template/page-2"},
  {id: "template-page-seven", pageNumber: 7, width: 1200, height: 900, imageUrl: "/template/page-7"}
];

const fragment = (
  regionKey: string,
  overrides: Partial<QuestionFrameFragment> = {}
): QuestionFrameFragment => ({
  regionKey,
  templatePageId: "template-page-two",
  pageNumber: 2,
  x: 0.1,
  y: 0.1,
  width: 0.3,
  height: 0.2,
  sortOrder: 0,
  source: "model",
  confidence: 0.78,
  issues: [],
  ...overrides
});

const item = (
  questionId: string,
  fragments: QuestionFrameFragment[],
  overrides: Partial<QuestionFrameItem> = {}
): QuestionFrameItem => ({
  questionId,
  status: "pending",
  revision: 4,
  fragments,
  issues: [],
  carriedFromItemId: null,
  confirmedAt: null,
  confirmedBy: null,
  ...overrides
});

const currentItem = item("question-arbitrary-alpha", [
  fragment("alpha-part-one"),
  fragment("alpha-part-two", {
    templatePageId: "template-page-seven",
    pageNumber: 7,
    x: 0.15,
    y: 0.6,
    width: 0.7,
    height: 0.25,
    sortOrder: 1
  })
]);

const q8CandidateOracle = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "backend/tests/fixtures/q8_full_frame_oracle.json"),
    "utf8"
  )
) as {
  reviewStatus: string;
  reviewedBy: string | null;
  reviewedAt: string | null;
  frameRegions: Array<{regionKey: string; pageNumber: number; polygon: Array<{x: number; y: number}>}>;
};

const defaultProps: TemplateQuestionFrameEditorProps = {
  pages,
  questionNumber: "探究题 A-27",
  questionConfirmationStatus: "confirmed",
  currentItem,
  otherItems: [{
    questionNumber: "附加题 β",
    item: item("question-beta", [fragment("beta-only", {x: 0.55, width: 0.35})], {
      status: "confirmed",
      revision: 8,
      confirmedAt: "2026-08-09T12:00:00Z",
      confirmedBy: "teacher"
    })
  }],
  onSave: vi.fn(async ({expectedRevision, regions}) => ({revision: expectedRevision + 1, regions})),
  onRerecognize: vi.fn(async ({expectedRevision, regions}) => ({
    revision: expectedRevision + 1,
    regions,
    status: "pending" as const,
    teacherOverridePreserved: false
  })),
  onConfirm: vi.fn(async ({expectedRevision}) => ({revision: expectedRevision + 1, status: "confirmed" as const}))
};

const mockCanvasBounds = (canvas: SVGSVGElement): void => {
  Object.defineProperty(canvas, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 500,
      bottom: 1000,
      width: 500,
      height: 1000,
      toJSON: () => ({})
    })
  });
};

describe("template question-frame review", () => {
  it("renders arbitrary question numbers, other questions, and cross-page fragments in natural coordinates", () => {
    const {container} = render(<TemplateQuestionFrameEditor {...defaultProps} />);

    expect(screen.getByRole("heading", {name: "题框：探究题 A-27"})).toBeInTheDocument();
    expect(screen.getByLabelText("题目确认状态")).toHaveTextContent("题目确认：已确认");
    expect(screen.getByLabelText("题框确认状态")).toHaveTextContent("题框确认：待确认");
    expect(screen.getByText("草稿题框")).toBeInTheDocument();
    expect(screen.getByText("已确认题框")).toBeInTheDocument();
    expect(screen.getByText("问题题框")).toBeInTheDocument();
    expect(screen.getAllByText("模型建议").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", {name: "确认题框"})).toBeInTheDocument();

    const firstCanvas = screen.getByLabelText("第 2 页题框编辑画布");
    expect(firstCanvas).toHaveAttribute("viewBox", "0 0 1000 2000");
    expect(container.querySelector('[data-region-key="alpha-part-one"]')).toBeInTheDocument();
    expect(container.querySelector('[data-region-key="beta-only"]')).toHaveClass("question-frame-editor__box--confirmed");
    expect(container.querySelector('[data-region-key="alpha-part-one"]')?.parentElement).toHaveClass("is-selected");

    fireEvent.click(screen.getByRole("button", {name: "查看第 7 页"}));
    expect(screen.getByLabelText("第 7 页题框编辑画布")).toHaveAttribute("viewBox", "0 0 1200 900");
    expect(container.querySelector('[data-region-key="alpha-part-two"]')).toBeInTheDocument();
    expect(container.querySelector('[data-region-key="alpha-part-one"]')).not.toBeInTheDocument();
  });

  it("shows the candidate q8 complete frame without extending into the following question", () => {
    expect(q8CandidateOracle.reviewStatus).toBe("candidate");
    expect(q8CandidateOracle.reviewedBy).toBeNull();
    expect(q8CandidateOracle.reviewedAt).toBeNull();
    const polygon = q8CandidateOracle.frameRegions[0].polygon;
    const xs = polygon.map(({x}) => x);
    const ys = polygon.map(({y}) => y);
    const q8Item = item("candidate-q8", [fragment("q8-candidate-full-frame", {
      templatePageId: "candidate-q8-page",
      pageNumber: 3,
      x: Math.min(...xs),
      y: Math.min(...ys),
      width: Math.max(...xs) - Math.min(...xs),
      height: Math.max(...ys) - Math.min(...ys)
    })]);
    const {container} = render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        pages={[{id: "candidate-q8-page", pageNumber: 3, width: 1000, height: 1400, imageUrl: "/candidate-q8"}]}
        questionNumber="8"
        currentItem={q8Item}
        otherItems={[]}
      />
    );

    expect(screen.getByLabelText("第 3 页题框编辑画布")).toHaveAttribute("viewBox", "0 0 1000 1400");
    const frame = container.querySelector('[data-region-key="q8-candidate-full-frame"]');
    expect(frame).toHaveAttribute("x", "95");
    expect(frame).toHaveAttribute("y", "518");
    expect(Number(frame?.getAttribute("width"))).toBeCloseTo(820);
    expect(Number(frame?.getAttribute("height"))).toBeCloseTo(609);
    expect(Number(frame?.getAttribute("y")) + Number(frame?.getAttribute("height"))).toBeLessThan(1200);
  });

  it("drags, resizes in eight directions, and saves normalized regions with the expected revision", async () => {
    const onSave = vi.fn(async ({expectedRevision, regions}) => ({revision: expectedRevision + 1, regions}));
    const single = item("question-without-numeric-id", [fragment("movable")]);
    const {container} = render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        currentItem={single}
        questionNumber="实验探究甲"
        otherItems={[]}
        onSave={onSave}
      />
    );
    const canvas = screen.getByLabelText("第 2 页题框编辑画布") as unknown as SVGSVGElement;
    mockCanvasBounds(canvas);
    const box = container.querySelector('[data-region-key="movable"]')!;

    expect(container.querySelectorAll("[data-resize-handle]")).toHaveLength(8);
    fireEvent.pointerDown(box, {pointerId: 1, clientX: 50, clientY: 100});
    fireEvent.pointerMove(canvas, {pointerId: 1, clientX: 100, clientY: 150});
    fireEvent.pointerUp(canvas, {pointerId: 1, clientX: 100, clientY: 150});
    expect(box).toHaveAttribute("x", "200");
    expect(Number(box.getAttribute("y"))).toBeCloseTo(300);

    const eastHandle = container.querySelector('[data-resize-handle="e"]')!;
    fireEvent.pointerDown(eastHandle, {pointerId: 2, clientX: 250, clientY: 250});
    fireEvent.pointerMove(canvas, {pointerId: 2, clientX: 300, clientY: 250});
    fireEvent.pointerUp(canvas, {pointerId: 2, clientX: 300, clientY: 250});
    expect(box).toHaveAttribute("width", "400");
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: "保存题框修改"}));
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    const payload = onSave.mock.calls[0][0];
    expect(payload).toMatchObject({
      questionId: "question-without-numeric-id",
      expectedRevision: 4,
      regions: [expect.objectContaining({
        regionKey: "movable",
        source: "teacher",
        confidence: null,
        issues: []
      })]
    });
    expect(payload.regions[0].x).toBeCloseTo(0.2);
    expect(payload.regions[0].y).toBeCloseTo(0.15);
    expect(payload.regions[0].width).toBeCloseTo(0.4);
    expect(payload.regions[0].height).toBeCloseTo(0.2);
    await waitFor(() => expect(screen.getByText("修改已保存")).toBeInTheDocument());
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
  });

  it("supports redraw, fragment add/delete, and undoing all unsaved edits", () => {
    const {container} = render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        currentItem={item("question-tools", [fragment("tool-fragment")])}
        questionNumber="工具题"
        otherItems={[]}
      />
    );
    const canvas = screen.getByLabelText("第 2 页题框编辑画布") as unknown as SVGSVGElement;
    mockCanvasBounds(canvas);

    fireEvent.click(screen.getByRole("button", {name: "重画选中片段"}));
    fireEvent.pointerDown(canvas, {pointerId: 3, clientX: 100, clientY: 200});
    fireEvent.pointerMove(canvas, {pointerId: 3, clientX: 400, clientY: 700});
    fireEvent.pointerUp(canvas, {pointerId: 3, clientX: 400, clientY: 700});
    expect(container.querySelector('[data-region-key="tool-fragment"]')).toHaveAttribute("x", "200");
    expect(container.querySelector('[data-region-key="tool-fragment"]')).toHaveAttribute("width", "600");

    fireEvent.click(screen.getByRole("button", {name: "增加题框片段"}));
    expect(container.querySelectorAll("[data-current-question-region]")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", {name: "删除选中片段"}));
    expect(container.querySelectorAll("[data-current-question-region]")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", {name: "撤销未保存修改"}));
    const restored = container.querySelector('[data-region-key="tool-fragment"]');
    expect(restored).toHaveAttribute("x", "100");
    expect(restored).toHaveAttribute("y", "200");
    expect(restored).toHaveAttribute("width", "300");
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
  });

  it("submits all cross-page draft fragments in one rerecognition action", async () => {
    const onSave = vi.fn(async ({expectedRevision, regions}) => ({
      revision: expectedRevision + 1,
      regions
    }));
    const onRerecognize = vi.fn(async ({expectedRevision, regions}) => ({
      revision: expectedRevision + 1,
      regions,
      status: "pending" as const,
      teacherOverridePreserved: false
    }));
    render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        currentItem={item("question-cross-page", [fragment("first-page")])}
        questionNumber="12"
        otherItems={[]}
        onSave={onSave}
        onRerecognize={onRerecognize}
      />
    );

    fireEvent.click(screen.getByRole("button", {name: "查看第 7 页"}));
    fireEvent.click(screen.getByRole("button", {name: "增加题框片段"}));
    fireEvent.click(screen.getByRole("button", {name: "保存并重新识别本题"}));

    await waitFor(() => expect(onRerecognize).toHaveBeenCalledOnce());
    expect(onSave).not.toHaveBeenCalled();
    expect(onRerecognize).toHaveBeenCalledWith({
      questionId: "question-cross-page",
      expectedRevision: 4,
      regions: [
        expect.objectContaining({regionKey: "first-page", pageNumber: 2, sortOrder: 0}),
        expect.objectContaining({pageNumber: 7, sortOrder: 1, source: "teacher"})
      ]
    });
    await waitFor(() => expect(
      screen.getByText("本题原文已重新识别，请重新确认题目和题框")
    ).toBeInTheDocument());
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
  });

  it("allows rerecognition without a dirty draft and reports preserved teacher content", async () => {
    const onRerecognize = vi.fn(async ({expectedRevision, regions}) => ({
      revision: expectedRevision + 1,
      regions,
      status: "pending" as const,
      teacherOverridePreserved: true
    }));
    render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        onRerecognize={onRerecognize}
      />
    );

    fireEvent.click(screen.getByRole("button", {name: "保存并重新识别本题"}));
    await waitFor(() => expect(onRerecognize).toHaveBeenCalledOnce());
    expect(screen.getByText(/当前仍显示教师修改内容/)).toBeInTheDocument();
  });

  it("adopts the saved frame revision when recognition fails after saving", async () => {
    const changedRegions = [fragment("saved-after-failure", {y: 0.22, source: "teacher"})];
    const failure = Object.assign(new Error("模型超时"), {
      code: "MODEL_TIMEOUT",
      details: {
        savedFrameSet: {
          revision: 5,
          items: [{questionId: "question-failure", status: "pending", fragments: changedRegions}]
        }
      }
    });
    const onRerecognize = vi.fn()
      .mockRejectedValueOnce(failure)
      .mockImplementationOnce(async ({expectedRevision, regions}) => ({
        revision: expectedRevision + 1,
        regions,
        status: "pending" as const
      }));
    render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        currentItem={item("question-failure", [fragment("before-failure")])}
        questionNumber="失败重试题"
        otherItems={[]}
        onRerecognize={onRerecognize}
      />
    );

    fireEvent.click(screen.getByRole("button", {name: "保存并重新识别本题"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("模型超时");
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "保存并重新识别本题"}));

    await waitFor(() => expect(onRerecognize).toHaveBeenCalledTimes(2));
    expect(onRerecognize.mock.calls[1][0]).toMatchObject({
      questionId: "question-failure",
      expectedRevision: 5,
      regions: [expect.objectContaining({regionKey: "saved-after-failure", y: 0.22})]
    });
  });

  it("keeps the local draft when a revision conflict is returned", async () => {
    const conflict = Object.assign(new Error("revision conflict"), {status: 409});
    const onSave = vi.fn(async () => Promise.reject(conflict));
    const conflictItem = item("question-conflict", [fragment("conflict-fragment")]);
    const {container, rerender} = render(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        currentItem={conflictItem}
        questionNumber="冲突题"
        otherItems={[]}
        onSave={onSave}
      />
    );
    const canvas = screen.getByLabelText("第 2 页题框编辑画布") as unknown as SVGSVGElement;
    mockCanvasBounds(canvas);
    const box = container.querySelector('[data-region-key="conflict-fragment"]')!;

    fireEvent.pointerDown(box, {pointerId: 4, clientX: 50, clientY: 100});
    fireEvent.pointerMove(canvas, {pointerId: 4, clientX: 100, clientY: 100});
    fireEvent.pointerUp(canvas, {pointerId: 4, clientX: 100, clientY: 100});
    fireEvent.click(screen.getByRole("button", {name: "保存题框修改"}));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地草稿已保留");
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(container.querySelector('[data-region-key="conflict-fragment"]')).toHaveAttribute("x", "200");

    rerender(
      <TemplateQuestionFrameEditor
        {...defaultProps}
        currentItem={item("question-conflict", [fragment("conflict-fragment", {x: 0.7})], {revision: 5})}
        questionNumber="冲突题"
        otherItems={[]}
        onSave={onSave}
      />
    );
    expect(container.querySelector('[data-region-key="conflict-fragment"]')).toHaveAttribute("x", "200");
    fireEvent.click(screen.getByRole("button", {name: "撤销未保存修改"}));
    await waitFor(() => expect(container.querySelector('[data-region-key="conflict-fragment"]')).toHaveAttribute("x", "700"));
  });

  it("blocks frame confirmation for dirty drafts or geometry blockers and confirms clean frames independently", async () => {
    const onConfirm = vi.fn(async ({expectedRevision}) => ({revision: expectedRevision + 1, status: "confirmed" as const}));
    const props = {
      ...defaultProps,
      currentItem: item("question-confirm", [fragment("confirm-fragment")]),
      questionNumber: "确认题",
      otherItems: [],
      questionConfirmationStatus: "confirmed" as const,
      geometryBlockers: ["题框与相邻题严重重叠"],
      onConfirm
    };
    const {rerender} = render(<TemplateQuestionFrameEditor {...props} />);
    expect(screen.getByRole("button", {name: "确认题框"})).toBeDisabled();
    expect(screen.getByText("题框与相邻题严重重叠")).toBeInTheDocument();

    rerender(<TemplateQuestionFrameEditor {...props} geometryBlockers={[]} />);
    expect(screen.getByRole("button", {name: "确认题框"})).toBeEnabled();
    fireEvent.click(screen.getByRole("button", {name: "增加题框片段"}));
    expect(screen.getByRole("button", {name: "确认题框"})).toBeDisabled();
    fireEvent.click(screen.getByRole("button", {name: "撤销未保存修改"}));
    fireEvent.click(screen.getByRole("button", {name: "确认题框"}));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({questionId: "question-confirm", expectedRevision: 4}));
    expect(screen.getByLabelText("题框确认状态")).toHaveTextContent("题框确认：已确认");
    expect(screen.getByLabelText("题目确认状态")).toHaveTextContent("题目确认：已确认");
  });
});
