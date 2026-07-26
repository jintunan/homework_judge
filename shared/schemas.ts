import { z } from "zod";
import { isQuestionTypeAllowed } from "./subject-profiles.js";

export const questionTypeSchema = z.enum([
  "choice",
  "fill_blank",
  "short_answer",
  "calculation",
]);

export const subjectSchema = z.enum([
  "middle_school_math",
  "high_school_physics",
]);

export const answerModeSchema = z.enum([
  "reference_upload",
  "agent_search",
]);

export const scoringPointSchema = z.object({
  description: z.string().trim().min(1, "请输入评分点说明").max(300),
  score: z.coerce.number().min(0, "评分点分值不能小于 0").max(100),
});

export const questionInputSchema = z
  .object({
    id: z.string().optional(),
    number: z.string().trim().min(1, "请输入题号").max(30),
    type: questionTypeSchema,
    maxScore: z.coerce.number().positive("满分必须大于 0").max(100),
    standardAnswer: z.string().trim().min(1, "请输入标准答案").max(4000),
    scoringPoints: z.array(scoringPointSchema).max(20).default([]),
    sortOrder: z.coerce.number().int().min(0),
  })
  .superRefine((question, context) => {
    const pointTotal = question.scoringPoints.reduce(
      (sum, point) => sum + point.score,
      0,
    );
    if (pointTotal > question.maxScore + 1e-8) {
      context.addIssue({
        code: "custom",
        path: ["scoringPoints"],
        message: "评分点分值合计不能超过题目满分",
      });
    }
  });

export const questionsBatchSchema = z
  .object({
    questions: z
      .array(questionInputSchema)
      .min(1, "至少录入一道题")
      .max(100, "首版最多支持 100 道题"),
  })
  .superRefine((value, context) => {
    const seen = new Map<string, number>();
    value.questions.forEach((question, index) => {
      const previous = seen.get(question.number);
      if (previous !== undefined) {
        context.addIssue({
          code: "custom",
          path: ["questions", index, "number"],
          message: `题号与第 ${previous + 1} 题重复`,
        });
      } else {
        seen.set(question.number, index);
      }
    });
  });

export const taskFieldsSchema = z.object({
  name: z.string().trim().min(2, "任务名称至少 2 个字").max(80),
  className: z.string().trim().min(1, "请输入班级").max(80),
  paperName: z.string().trim().min(2, "试卷名称至少 2 个字").max(120),
  subject: subjectSchema.default("middle_school_math"),
  answerMode: answerModeSchema.default("agent_search"),
});

export const taskUpdateSchema = taskFieldsSchema.partial().refine(
  (value) => Object.keys(value).length > 0,
  "至少提供一个需要修改的字段",
);

export const studentNameSchema = z.object({
  studentName: z.string().trim().min(1, "请输入学生姓名").max(60),
});

export const reviewUpdateSchema = z.object({
  finalAnswer: z.string().trim().max(4000),
  finalScore: z.coerce.number().min(0, "得分不能小于 0").max(100),
  teacherComment: z.string().trim().max(2000),
  reviewStatus: z.enum(["pending", "needs_attention", "reviewed"]),
});

export const modelQuestionOutputSchema = z.object({
  questionNumber: z.string().trim().min(1),
  recognizedAnswer: z.string().default(""),
  suggestedScore: z.coerce.number(),
  reason: z.string().default(""),
  confidence: z.coerce.number().min(0).max(1),
  needsAttention: z.boolean().default(false),
});

export const modelOutputSchema = z.object({
  questions: z.array(modelQuestionOutputSchema),
  overallNote: z.string().optional(),
});

const httpUrlSchema = z
  .string()
  .url()
  .refine((value) => /^https?:\/\//i.test(value), "来源必须使用 HTTP(S)");

export const answerSourceSchema = z.object({
  title: z.string().trim().min(1).max(300),
  url: httpUrlSchema,
  snippet: z.string().trim().max(1000).default(""),
});

export const answerCandidateSchema = z
  .object({
    questionNumber: z.string().trim().min(1).max(30),
    questionText: z.string().trim().min(1).max(8000),
    type: questionTypeSchema,
    maxScore: z.coerce.number().positive().max(100),
    standardAnswer: z.string().trim().max(8000).default(""),
    scoringPoints: z.array(scoringPointSchema).max(30).default([]),
    reason: z.string().trim().max(4000).default(""),
    confidence: z.coerce.number().min(0).max(1),
    needsAttention: z.boolean().default(false),
  })
  .superRefine((value, context) => {
    const total = value.scoringPoints.reduce(
      (sum, point) => sum + point.score,
      0,
    );
    if (total > value.maxScore + 1e-8) {
      context.addIssue({
        code: "custom",
        path: ["scoringPoints"],
        message: "评分点分值合计不能超过题目满分",
      });
    }
  });

export const extractedPaperSchema = z
  .object({
    questions: z.array(answerCandidateSchema).min(1).max(100),
    overallNote: z.string().max(4000).optional(),
  })
  .superRefine((value, context) => {
    const seen = new Set<string>();
    value.questions.forEach((question, index) => {
      if (seen.has(question.questionNumber)) {
        context.addIssue({
          code: "custom",
          path: ["questions", index, "questionNumber"],
          message: "题号重复",
        });
      }
      seen.add(question.questionNumber);
    });
  });

export const searchedAnswerSchema = z.object({
  found: z.boolean(),
  standardAnswer: z.string().trim().max(8000).default(""),
  scoringPoints: z.array(scoringPointSchema).max(30).default([]),
  reason: z.string().trim().max(4000).default(""),
  confidence: z.coerce.number().min(0).max(1).default(0),
  sources: z.array(answerSourceSchema).max(10).default([]),
});

export const generatedAnswerSchema = searchedAnswerSchema
  .omit({ found: true, sources: true })
  .extend({
    standardAnswer: z.string().trim().min(1).max(8000),
  });

export const answerDraftUpdateSchema = z
  .object({
    number: z.string().trim().min(1).max(30),
    type: questionTypeSchema,
    maxScore: z.coerce.number().positive().max(100),
    standardAnswer: z.string().trim().min(1).max(8000),
    scoringPoints: z.array(scoringPointSchema).max(30),
  })
  .superRefine((value, context) => {
    const total = value.scoringPoints.reduce(
      (sum, point) => sum + point.score,
      0,
    );
    if (total > value.maxScore + 1e-8) {
      context.addIssue({
        code: "custom",
        path: ["scoringPoints"],
        message: "评分点分值合计不能超过题目满分",
      });
    }
  });

export const answerDraftActionSchema = z.object({
  reason: z.string().trim().max(2000).default(""),
});

export function validateQuestionTypeForSubject(
  subject: z.infer<typeof subjectSchema>,
  type: z.infer<typeof questionTypeSchema>,
): boolean {
  return isQuestionTypeAllowed(subject, type);
}

export type QuestionInput = z.input<typeof questionInputSchema>;
export type QuestionsBatchInput = z.output<typeof questionsBatchSchema>;
export type ModelOutput = z.output<typeof modelOutputSchema>;
