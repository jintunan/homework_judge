import type {GradingBlankDefinition} from "@shared/contracts";

function parseCents(value: string | number | null | undefined): number | null {
  const text = String(value ?? "").trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return Number.isSafeInteger(cents) ? cents : null;
}

function formatCents(value: number): string {
  return `${Math.floor(value / 100)}.${String(value % 100).padStart(2, "0")}`;
}

export function allocateBlankScoreStrings(
  maxScore: string | number | null | undefined,
  blankCount: number
): string[] {
  const total = parseCents(maxScore);
  if (total == null || total <= 0 || blankCount < 1) return [];
  const roundedAverage = Math.floor(total / blankCount + 0.5);
  const last = total - roundedAverage * (blankCount - 1);
  if (last <= 0) {
    if (total < blankCount) return [];
    const floorAverage = Math.floor(total / blankCount);
    const extra = total % blankCount;
    return Array.from(
      {length: blankCount},
      (_, index) => formatCents(floorAverage + (index < extra ? 1 : 0))
    );
  }
  return [
    ...Array.from({length: blankCount - 1}, () => formatCents(roundedAverage)),
    formatCents(last)
  ];
}

export function rebalanceBlanks(
  blanks: GradingBlankDefinition[],
  maxScore: string | number | null | undefined
): GradingBlankDefinition[] {
  const scores = allocateBlankScoreStrings(maxScore, blanks.length);
  return blanks.map((blank, index) => ({
    ...blank,
    blankKey: `B${index + 1}`,
    sortOrder: index,
    maxScore: scores[index] ?? ""
  }));
}

export function blankConfigErrors(
  blanks: GradingBlankDefinition[],
  maxScore: string | number | null | undefined
): {rows: string[]; total: string | null} {
  const rows = blanks.map((blank, index) => {
    if (!blank.standardAnswers.some((answer) => answer.trim())) {
      return `第 ${index + 1} 空缺少标准答案`;
    }
    const cents = parseCents(blank.maxScore);
    if (cents == null || cents <= 0) return `第 ${index + 1} 空分值必须大于 0`;
    return "";
  });
  const expected = parseCents(maxScore);
  const values = blanks.map((blank) => parseCents(blank.maxScore));
  const actual = values.every((value) => value != null)
    ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0)
    : null;
  const total = expected == null || actual == null || expected === actual
    ? null
    : `逐空分值合计 ${formatCents(actual)}，必须等于本题满分 ${formatCents(expected)}`;
  return {rows, total};
}
