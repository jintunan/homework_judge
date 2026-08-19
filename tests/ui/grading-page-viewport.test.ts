import { describe, expect, it } from "vitest";
import {
  calculatePageViewport,
  clampPageZoom
} from "../../client/src/features/grading/page-viewport";

describe("grading page viewport", () => {
  it.each([
    [1366, 768],
    [1440, 900],
    [1920, 1080]
  ])("keeps a 1697x2400 page inside a %ix%i fit-page viewport", (width, height) => {
    const result = calculatePageViewport({
      pageWidth: 1697,
      pageHeight: 2400,
      viewportWidth: width,
      viewportHeight: height,
      mode: "fit-page",
      zoom: 1,
      padding: 32
    });

    expect(result.width).toBeLessThanOrEqual(width - 32 + .5);
    expect(result.height).toBeLessThanOrEqual(height - 32 + .5);
    expect(result.overflowY).toBe(false);
  });

  it("allows explicit vertical overflow only in width or enlarged modes", () => {
    const fitWidth = calculatePageViewport({
      pageWidth: 1000,
      pageHeight: 2000,
      viewportWidth: 800,
      viewportHeight: 600,
      mode: "fit-width",
      zoom: 1,
      padding: 32
    });
    const actual = calculatePageViewport({
      pageWidth: 1000,
      pageHeight: 2000,
      viewportWidth: 800,
      viewportHeight: 600,
      mode: "actual",
      zoom: 1,
      padding: 32
    });

    expect(fitWidth.overflowY).toBe(true);
    expect(actual.scale).toBe(1);
    expect(actual.overflowX).toBe(true);
  });

  it("clamps the explicit zoom multiplier safely", () => {
    expect(clampPageZoom(-10)).toBe(.25);
    expect(clampPageZoom(4)).toBe(3);
    expect(clampPageZoom(Number.NaN)).toBe(1);
  });
});
