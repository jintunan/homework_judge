import {describe, expect, it} from "vitest";
import {
  drawNormalizedRect,
  moveNormalizedRect,
  normalizedToPixels,
  pixelsToNormalized,
  resizeNormalizedRect,
  updateFragmentGeometry
} from "../../client/src/features/review/question-frame-geometry";
import type {QuestionFrameFragment} from "../../shared/contracts";

const page = {width: 1000, height: 2000};

describe("question-frame editor geometry", () => {
  it("round-trips normalized coordinates independently of the rendered scale", () => {
    const normalized = {x: 0.125, y: 0.2, width: 0.75, height: 0.35};
    const pixels = normalizedToPixels(normalized, page);
    expect(pixels).toEqual({x: 125, y: 400, width: 750, height: 700});
    expect(pixelsToNormalized(pixels, page)).toEqual(normalized);

    const doubleSize = {width: 2000, height: 4000};
    expect(pixelsToNormalized(normalizedToPixels(normalized, doubleSize), doubleSize)).toEqual(normalized);
  });

  it("clamps dragging to the page without changing the rectangle size", () => {
    const moved = moveNormalizedRect(
      {x: 0.1, y: 0.15, width: 0.4, height: 0.3},
      {x: -500, y: 1900},
      page
    );
    expect(moved).toEqual({x: 0, y: 0.7, width: 0.4, height: 0.3});
  });

  it.each(["n", "ne", "e", "se", "s", "sw", "w", "nw"] as const)(
    "resizes from the %s handle while keeping a positive in-page rectangle",
    (handle) => {
      const resized = resizeNormalizedRect(
        {x: 0.2, y: 0.2, width: 0.4, height: 0.4},
        handle,
        {x: handle.includes("w") ? 500 : -500, y: handle.includes("n") ? 900 : -900},
        page,
        12
      );
      expect(resized.x).toBeGreaterThanOrEqual(0);
      expect(resized.y).toBeGreaterThanOrEqual(0);
      expect(resized.width).toBeGreaterThanOrEqual(0.012);
      expect(resized.height).toBeGreaterThanOrEqual(0.006);
      expect(resized.x + resized.width).toBeLessThanOrEqual(1);
      expect(resized.y + resized.height).toBeLessThanOrEqual(1);
    }
  );

  it("draws in any pointer direction and rejects boxes below the minimum size", () => {
    expect(drawNormalizedRect({x: 800, y: 1500}, {x: 200, y: 500}, page, 10)).toEqual({
      x: 0.2,
      y: 0.25,
      width: 0.6,
      height: 0.5
    });
    expect(drawNormalizedRect({x: 10, y: 10}, {x: 15, y: 15}, page, 10)).toBeNull();
  });

  it("changes only geometry and preserves generic fragment identity and page ownership", () => {
    const fragment: QuestionFrameFragment = {
      regionKey: "arbitrary-question-page-7-part-2",
      templatePageId: "template-page-7",
      pageNumber: 7,
      sortOrder: 1,
      source: "teacher",
      confidence: null,
      issues: [],
      x: 0.1,
      y: 0.2,
      width: 0.3,
      height: 0.4
    };
    const changed = updateFragmentGeometry(fragment, {x: 0.2, y: 0.3, width: 0.5, height: 0.2});
    expect(changed).toMatchObject({
      regionKey: fragment.regionKey,
      templatePageId: fragment.templatePageId,
      pageNumber: 7,
      sortOrder: 1,
      x: 0.2,
      y: 0.3,
      width: 0.5,
      height: 0.2
    });
  });
});
