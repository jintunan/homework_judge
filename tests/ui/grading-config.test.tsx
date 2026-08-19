import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GradingConfig, ReviewQuestion } from "@shared/contracts";
import { GradingConfigPanel } from "@/features/grading/GradingConfigPanel";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: vi.fn(),
  applyAnswerGradingDraft: vi.fn(),
  generateAnswerGradingDraft: vi.fn()
}));

const question: ReviewQuestion = {
  id: "question",
  sortOrder: 0,
  original: {
    number: "5",
    stem: "物体因______电子而带正电，同种电荷相互______，异种电荷相互______。",
    options: [],
    type: "fill_blank",
    score: 4
  },
  effective: {
    number: "5",
    stem: "物体因______电子而带正电，同种电荷相互______，异种电荷相互______。",
    options: [],
    type: "fill_blank",
    score: 4
  },
  sourcePages: [1],
  answerRegions: [],
  confidence: 1,
  issues: [],
  isDuplicate: false,
  confirmationStatus: "confirmed",
  match: {
    id: "match",
    answerEntryId: "answer",
    method: "manual",
    numberScore: 1,
    stemScore: 1,
    orderScore: 1,
    totalScore: 1,
    reasons: [],
    status: "confirmed",
    answer: "失去 异种 吸引",
    explanation: "",
    answerSourcePages: [1]
  }
};

const calculationQuestion: ReviewQuestion = {
  ...question,
  id: "calculation-question",
  original: {...question.original, type: "calculation", score: 10},
  effective: {...question.effective, type: "calculation", score: 10}
};

function derivedConfig(answers: string[][]): GradingConfig {
  return {
    questionId: "question",
    questionType: "fill_blank",
    maxScore: "4.00",
    configVersion: 0,
    frameSetId: "frame-set",
    confirmationStatus: "confirmed",
    blanks: answers.map((standardAnswers, index) => ({
      blankKey: `B${index + 1}`,
      sortOrder: index,
      maxScore: ["1.33", "1.33", "1.34"][index],
      answerKind: "text",
      standardAnswers,
      synonyms: []
    })),
    initialization: {
      source: "derived",
      autoConfirmable: true,
      blockingReasons: [],
      signals: {
        stemMarkerCount: answers.length,
        independentRegionCount: 1,
        structuredAnswerCount: null,
        selectedCount: answers.length
      },
      warnings: [{code: "composite_region_shared", message: "多个空正在共享一个复合答题区域，请检查区域是否准确。"}]
    }
  };
}

describe("fill-blank grading config", () => {
  beforeEach(() => vi.mocked(api).mockReset());

  it("explains that a confirmed item still needs the whole frame set frozen", async () => {
    vi.mocked(api).mockResolvedValueOnce(derivedConfig([["失去"], ["异种"], ["吸引"]]));
    render(<GradingConfigPanel
      question={{
        ...question,
        questionFrame: {
          questionId: question.id,
          status: "confirmed",
          revision: 1,
          fragments: [],
          issues: [],
          carriedFromItemId: null,
          confirmedAt: "2026-08-14T00:00:00Z",
          confirmedBy: "teacher"
        }
      }}
      frameSetStatus="draft"
    />);

    expect(await screen.findByText("本题题框已确认，但整套题框尚未冻结")).toBeInTheDocument();
    expect(screen.getByText(/点击页面顶部的“冻结整套题框”/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "保存批改设置"})).toBeDisabled();
  });

  it("renders every derived blank and saves the reviewed configuration", async () => {
    const derived = derivedConfig([["失去"], ["异种"], ["吸引"]]);
    const saved: GradingConfig = {
      ...derived,
      configVersion: 1,
      initialization: {
        source: "saved",
        autoConfirmable: false,
        blockingReasons: [],
        signals: null,
        warnings: []
      }
    };
    vi.mocked(api).mockResolvedValueOnce(derived).mockResolvedValueOnce(saved);

    render(<GradingConfigPanel question={question} />);

    expect(await screen.findByText(/已识别 3 个空并生成默认分值/)).toBeInTheDocument();
    expect(screen.getByText(/无需逐空手工保存/)).toBeInTheDocument();
    expect(screen.getByText(/未单独定位的空将使用完整题框作为共享识别范围/)).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 空标准答案")).toHaveValue("失去");
    expect(screen.getByLabelText("第 2 空标准答案")).toHaveValue("异种");
    expect(screen.getByLabelText("第 3 空标准答案")).toHaveValue("吸引");
    expect(screen.getByLabelText("第 1 空分值")).toHaveValue(1.33);
    expect(screen.getByLabelText("第 3 空分值")).toHaveValue(1.34);

    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("spinbutton", {name: "本题满分"})).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("第 1 空分值")).toHaveFocus();
    await user.tab();
    expect(screen.getAllByRole("combobox")[0]).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("第 1 空标准答案")).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("第 1 空同义答案")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", {name: "删除第 1 空"})).toHaveFocus();

    fireEvent.click(screen.getByRole("button", {name: "增加一空"}));
    expect(screen.getByLabelText("第 4 空标准答案")).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 空分值")).toHaveValue(1);
    expect(screen.getByLabelText("第 4 空分值")).toHaveValue(1);
    fireEvent.click(screen.getByRole("button", {name: "删除第 4 空"}));
    expect(screen.queryByLabelText("第 4 空标准答案")).not.toBeInTheDocument();
    expect(screen.getByLabelText("第 1 空分值")).toHaveValue(1.33);
    expect(screen.getByLabelText("第 3 空分值")).toHaveValue(1.34);

    fireEvent.click(screen.getByRole("button", {name: "保存批改设置"}));
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    const request = vi.mocked(api).mock.calls[1][1];
    const requestBody = JSON.parse(String(request?.body));
    const requestBlanks = requestBody.blanks;
    expect(requestBody).toMatchObject({
      frameSetId: "frame-set",
      expectedConfigVersion: 0,
      confirm: true
    });
    expect(requestBlanks).toHaveLength(3);
    expect(requestBlanks.map((blank: {blankKey: string}) => blank.blankKey)).toEqual([
      "B1",
      "B2",
      "B3"
    ]);
    expect(requestBlanks.map((blank: {sortOrder: number}) => blank.sortOrder)).toEqual([0, 1, 2]);
    expect(await screen.findByText("评分配置已保存")).toBeInTheDocument();
    expect(screen.queryByText(/自动初始化尚未保存/)).not.toBeInTheDocument();
  });

  it("rebalances five points across a manually added third blank", async () => {
    const derived = derivedConfig([["电荷转移"], ["CD"]]);
    derived.maxScore = "5.00";
    derived.blanks = derived.blanks.map((blank) => ({...blank, maxScore: "2.50"}));
    vi.mocked(api).mockResolvedValueOnce(derived);

    render(<GradingConfigPanel question={{
      ...question,
      effective: {...question.effective, score: 5}
    }} />);

    await screen.findByLabelText("第 2 空标准答案");
    fireEvent.click(screen.getByRole("button", {name: "增加一空"}));

    expect(screen.getByLabelText("第 1 空分值")).toHaveValue(1.67);
    expect(screen.getByLabelText("第 2 空分值")).toHaveValue(1.67);
    expect(screen.getByLabelText("第 3 空分值")).toHaveValue(1.66);
    expect(screen.getByText("第 3 空缺少标准答案")).toBeInTheDocument();
  });

  it("does not put an ambiguous complete answer into the first blank", async () => {
    const derived = derivedConfig([[], [], []]);
    derived.initialization.autoConfirmable = false;
    derived.initialization.blockingReasons = [{
      code: "blank_standard_answer_missing",
      message: "至少一个空缺少标准答案，请逐空检查并保存。"
    }];
    derived.initialization.warnings.unshift({
      code: "answer_split_ambiguous",
      message: "答案分配需要检查：参考答案无法安全分配到 3 个空，请逐空检查。"
    });
    vi.mocked(api).mockResolvedValueOnce(derived);

    render(<GradingConfigPanel question={question} />);

    expect(await screen.findByText(/需要逐空检查并保存后才能批改/)).toBeInTheDocument();
    expect(screen.getByText("至少一个空缺少标准答案，请逐空检查并保存。")).toBeInTheDocument();
    expect(await screen.findByText(/答案分配需要检查/)).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 空标准答案")).toHaveValue("");
    expect(screen.getByLabelText("第 2 空标准答案")).toHaveValue("");
    expect(screen.getByLabelText("第 3 空标准答案")).toHaveValue("");
    expect(screen.queryByDisplayValue(question.match.answer)).not.toBeInTheDocument();
  });

  it("clears the previous derived state while switching questions", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(derivedConfig([["甲"], ["乙"], ["丙"]]))
      .mockResolvedValueOnce(derivedConfig([["左"], ["右"]]));
    const {rerender} = render(<GradingConfigPanel question={question} />);
    expect(await screen.findByText(/已识别 3 个空/)).toBeInTheDocument();

    rerender(<GradingConfigPanel question={{...question, id: "question-2"}} />);

    expect(screen.getByText("正在读取评分配置…")).toBeInTheDocument();
    expect(screen.queryByText(/已识别 3 个空/)).not.toBeInTheDocument();
    expect(await screen.findByText(/已识别 2 个空/)).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 空标准答案")).toHaveValue("左");
  });

  it("reloads the config version after the whole frame set is frozen", async () => {
    const initial = derivedConfig([["失去"], ["异种"], ["吸引"]]);
    const latest = {...initial, configVersion: 4};
    vi.mocked(api).mockResolvedValueOnce(initial).mockResolvedValueOnce(latest);

    const {rerender} = render(
      <GradingConfigPanel question={question} frameSetStatus="draft" />
    );
    expect(await screen.findByText("版本 0")).toBeInTheDocument();

    rerender(<GradingConfigPanel question={question} frameSetStatus="confirmed" />);

    expect(await screen.findByText("版本 4")).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("automatically reloads the newest version after a save conflict", async () => {
    const initial = derivedConfig([["失去"], ["异种"], ["吸引"]]);
    const latest = {...initial, configVersion: 5};
    const conflict = Object.assign(new Error("逐空配置已被其他修改覆盖，请重新加载后再保存"), {
      code: "BLANK_CONFIG_VERSION_CONFLICT"
    });
    vi.mocked(api)
      .mockResolvedValueOnce(initial)
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(latest);

    render(<GradingConfigPanel question={question} />);
    await screen.findByText("版本 0");
    fireEvent.click(screen.getByRole("button", {name: "保存批改设置"}));

    expect(await screen.findByText("配置已自动刷新到最新版本，请检查后重新保存")).toBeInTheDocument();
    expect(screen.getByText("版本 5")).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(3);
  });
});

describe("calculation rubric policy", () => {
  beforeEach(() => vi.mocked(api).mockReset());

  it("explains omitted-step evidence and alternative-method credit to teachers", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        questionId: "calculation-question",
        questionType: "calculation",
        maxScore: "10.00",
        configVersion: 1,
        frameSetId: null,
        confirmationStatus: "confirmed",
        blanks: [],
        initialization: {
          source: "none",
          autoConfirmable: false,
          blockingReasons: [],
          signals: null,
          warnings: []
        }
      })
      .mockResolvedValueOnce([{
        id: "rubric-draft",
        versionNumber: 2,
        status: "draft",
        maxScore: "10.00",
        points: [
          {pointKey: "P1", criterion: "关键关系", score: "8.00", sortOrder: 0, dependencies: []},
          {pointKey: "FINAL_ANSWER", criterion: "最终答案", score: "2.00", sortOrder: 1, dependencies: []}
        ]
      }]);

    render(<GradingConfigPanel question={calculationQuestion} />);

    expect(await screen.findByText(/省略的非关键步骤可由后续正确公式证明/)).toBeInTheDocument();
    expect(screen.getByText(/不同正确解法按作用等价的评分点给分/)).toBeInTheDocument();
  });

  it("does not freeze the stored rubric when saving the edited draft fails", async () => {
    const config: GradingConfig = {
      questionId: "calculation-question",
      questionType: "calculation",
      maxScore: "10.00",
      configVersion: 3,
      frameSetId: null,
      confirmationStatus: "confirmed",
      blanks: [],
      initialization: {
        source: "none",
        autoConfirmable: false,
        blockingReasons: [],
        signals: null,
        warnings: []
      }
    };
    const draft = {
      id: "rubric-draft",
      versionNumber: 4,
      status: "draft" as const,
      maxScore: "10.00",
      points: [
        {pointKey: "P1", criterion: "关键关系", score: "8.00", sortOrder: 0, dependencies: []},
        {pointKey: "FINAL_ANSWER", criterion: "最终答案", score: "2.00", sortOrder: 1, dependencies: []}
      ]
    };
    vi.mocked(api)
      .mockResolvedValueOnce(config)
      .mockResolvedValueOnce([draft])
      .mockRejectedValueOnce(new Error("草案版本冲突"));

    render(<GradingConfigPanel question={calculationQuestion} />);
    await screen.findByText("评分细则草案 v4");
    fireEvent.change(screen.getByLabelText("评分点 1 要求"), {target: {value: "教师修改后的要求"}});
    fireEvent.click(screen.getByRole("button", {name: /校验并冻结/}));

    expect(await screen.findByText("草案版本冲突")).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(3);
    expect(vi.mocked(api).mock.calls.some(([path]) => path === "/rubric-versions/rubric-draft/freeze")).toBe(false);
  });

  it("shows and reconfirms a frozen rubric invalidated by a later config timestamp", async () => {
    const config: GradingConfig = {
      questionId: "calculation-question",
      questionType: "calculation",
      maxScore: "10.00",
      configVersion: 2,
      frameSetId: null,
      confirmationStatus: "confirmed",
      blanks: [],
      initialization: {
        source: "none",
        autoConfirmable: false,
        blockingReasons: [],
        signals: null,
        warnings: []
      }
    };
    const stale = {
      id: "rubric-frozen",
      versionNumber: 1,
      status: "frozen" as const,
      maxScore: "10.00",
      frozenAt: "2026-08-14T00:00:00Z",
      isCurrent: false,
      points: [
        {pointKey: "P1", criterion: "列出关键公式", score: "8.00", sortOrder: 0, dependencies: []},
        {pointKey: "FINAL_ANSWER", criterion: "最终答案", score: "2.00", sortOrder: 1, dependencies: []}
      ]
    };
    vi.mocked(api)
      .mockResolvedValueOnce(config)
      .mockResolvedValueOnce([stale])
      .mockResolvedValueOnce({...stale, isCurrent: true});

    render(<GradingConfigPanel question={calculationQuestion} />);

    expect(await screen.findByText("评分细则 v1 需要重新确认")).toBeInTheDocument();
    expect(screen.getByText("P1：列出关键公式（8.00 分）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: /确认 v1 仍适用/}));

    expect(await screen.findByText("评分细则 v1 已按当前题目重新确认")).toBeInTheDocument();
    expect(screen.queryByText("评分细则 v1 需要重新确认")).not.toBeInTheDocument();
    expect(api).toHaveBeenLastCalledWith("/rubric-versions/rubric-frozen/freeze", {method: "POST"});
  });
});
