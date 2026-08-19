import { z } from "zod";

export const normalizedBoxSchema = z.object({
  x: z.number().min(0).max(1),
  y: z.number().min(0).max(1),
  width: z.number().positive().max(1),
  height: z.number().positive().max(1)
}).superRefine((box, context) => {
  if (box.x + box.width > 1 || box.y + box.height > 1) {
    context.addIssue({code: z.ZodIssueCode.custom, message: "box must stay within page bounds"});
  }
});

export const questionFrameFragmentSchema = normalizedBoxSchema.and(z.object({
  regionKey: z.string().min(1),
  templatePageId: z.string().min(1),
  pageNumber: z.number().int().positive(),
  sortOrder: z.number().int().nonnegative(),
  source: z.enum(["model", "teacher", "legacy"]),
  confidence: z.number().min(0).max(1).nullable(),
  issues: z.array(z.string())
}));

export const layeredIssueSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  layer: z.enum(["question_frame", "blank_config", "alignment", "recognition", "grading"]),
  questionId: z.string().nullable().optional(),
  questionNumber: z.string().nullable().optional(),
  regionKey: z.string().nullable().optional(),
  relatedQuestionId: z.string().nullable().optional(),
  relatedQuestionNumber: z.string().nullable().optional(),
  relatedRegionKey: z.string().nullable().optional(),
  details: z.record(z.string(), z.unknown()).optional(),
  nextAction: z.string().nullable().optional()
});

export const blankAnchorSchema = z.object({
  templatePageId: z.string().min(1),
  pageNumber: z.number().int().positive(),
  coordinateSpace: z.literal("template_page_normalized"),
  box: normalizedBoxSchema,
  source: z.enum(["model", "teacher", "legacy"]),
  confidence: z.number().min(0).max(1).nullable(),
  issues: z.array(z.string())
});

export const apiEnvelopeSchema = z.object({
  data: z.unknown().nullable(),
  error: z
    .object({
      code: z.string(),
      message: z.string(),
      details: z.unknown().nullable().optional()
    })
    .nullable()
});
