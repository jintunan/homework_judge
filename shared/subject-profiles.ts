import type { QuestionType, Subject } from "./contracts.js";

export interface SubjectProfile {
  subject: Subject;
  label: string;
  supportedTypes: QuestionType[];
  extractionInstructions: string;
  answerInstructions: string;
  scoringPointRules: string[];
}

export const subjectProfiles: Record<Subject, SubjectProfile> = {
  middle_school_math: {
    subject: "middle_school_math",
    label: "初中数学",
    supportedTypes: ["choice", "fill_blank", "short_answer"],
    extractionInstructions:
      "只提取选择题、填空题和简单简答题；作图、复杂证明和开放探究题标记为需人工处理。",
    answerInstructions:
      "答案应使用初中数学范围内的规范表达，简单简答题给出关键步骤，但不要输出隐藏思维过程。",
    scoringPointRules: [
      "选择题和填空题通常按最终答案给分",
      "简单简答题按关键列式、计算步骤和最终结论拆分评分点",
    ],
  },
  high_school_physics: {
    subject: "high_school_physics",
    label: "高中物理",
    supportedTypes: ["choice", "fill_blank", "calculation"],
    extractionInstructions:
      "只提取选择题、填空题和计算题；实验设计、作图和复杂综合题标记为需人工处理。",
    answerInstructions:
      "计算题给出关键公式、代入过程、数值结果和单位要求，不要输出隐藏思维过程。",
    scoringPointRules: [
      "选择题和填空题按最终答案及必要单位给分",
      "计算题按关键公式、代入过程、运算结果和单位拆分评分点",
    ],
  },
};

export function getSubjectProfile(subject: Subject): SubjectProfile {
  return subjectProfiles[subject];
}

export function isQuestionTypeAllowed(
  subject: Subject,
  type: QuestionType,
): boolean {
  return subjectProfiles[subject].supportedTypes.includes(type);
}

export function subjectLabel(subject: Subject): string {
  return subjectProfiles[subject].label;
}
