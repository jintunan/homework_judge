import type {NormalizedBox, QuestionFrameFragment} from "@shared/contracts";

export interface PixelSize {
  width: number;
  height: number;
}

export interface PixelPoint {
  x: number;
  y: number;
}

export interface PixelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type ResizeHandle = "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw";

const clamp = (value: number, minimum: number, maximum: number): number =>
  Math.min(Math.max(value, minimum), maximum);

const requirePageSize = (page: PixelSize): void => {
  if (!Number.isFinite(page.width) || !Number.isFinite(page.height) || page.width <= 0 || page.height <= 0) {
    throw new Error("page dimensions must be finite positive numbers");
  }
};

const requireFiniteRect = (rect: PixelRect | NormalizedBox): void => {
  if (![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)) {
    throw new Error("rectangle values must be finite");
  }
  if (rect.width <= 0 || rect.height <= 0) {
    throw new Error("rectangle dimensions must be positive");
  }
};

export const normalizedToPixels = (rect: NormalizedBox, page: PixelSize): PixelRect => {
  requirePageSize(page);
  requireFiniteRect(rect);
  return {
    x: rect.x * page.width,
    y: rect.y * page.height,
    width: rect.width * page.width,
    height: rect.height * page.height
  };
};

export const pixelsToNormalized = (rect: PixelRect, page: PixelSize): NormalizedBox => {
  requirePageSize(page);
  requireFiniteRect(rect);
  const left = clamp(rect.x, 0, page.width);
  const top = clamp(rect.y, 0, page.height);
  const right = clamp(rect.x + rect.width, left, page.width);
  const bottom = clamp(rect.y + rect.height, top, page.height);
  if (right <= left || bottom <= top) {
    throw new Error("rectangle must retain positive area inside the page");
  }
  return {
    x: left / page.width,
    y: top / page.height,
    width: (right - left) / page.width,
    height: (bottom - top) / page.height
  };
};

export const moveNormalizedRect = (
  rect: NormalizedBox,
  deltaPixels: PixelPoint,
  page: PixelSize
): NormalizedBox => {
  requirePageSize(page);
  requireFiniteRect(rect);
  if (!Number.isFinite(deltaPixels.x) || !Number.isFinite(deltaPixels.y)) {
    throw new Error("drag delta must be finite");
  }
  const width = Math.min(rect.width, 1);
  const height = Math.min(rect.height, 1);
  return {
    x: clamp(rect.x + deltaPixels.x / page.width, 0, 1 - width),
    y: clamp(rect.y + deltaPixels.y / page.height, 0, 1 - height),
    width,
    height
  };
};

export const resizeNormalizedRect = (
  rect: NormalizedBox,
  handle: ResizeHandle,
  deltaPixels: PixelPoint,
  page: PixelSize,
  minimumSizePixels = 8
): NormalizedBox => {
  requirePageSize(page);
  requireFiniteRect(rect);
  if (!Number.isFinite(deltaPixels.x) || !Number.isFinite(deltaPixels.y)) {
    throw new Error("resize delta must be finite");
  }
  if (!Number.isFinite(minimumSizePixels) || minimumSizePixels <= 0) {
    throw new Error("minimum size must be positive");
  }

  const source = normalizedToPixels(rect, page);
  let left = source.x;
  let top = source.y;
  let right = source.x + source.width;
  let bottom = source.y + source.height;
  const minWidth = Math.min(minimumSizePixels, page.width);
  const minHeight = Math.min(minimumSizePixels, page.height);

  if (handle.includes("w")) {
    left = clamp(left + deltaPixels.x, 0, right - minWidth);
  }
  if (handle.includes("e")) {
    right = clamp(right + deltaPixels.x, left + minWidth, page.width);
  }
  if (handle.includes("n")) {
    top = clamp(top + deltaPixels.y, 0, bottom - minHeight);
  }
  if (handle.includes("s")) {
    bottom = clamp(bottom + deltaPixels.y, top + minHeight, page.height);
  }

  return pixelsToNormalized(
    {x: left, y: top, width: right - left, height: bottom - top},
    page
  );
};

export const drawNormalizedRect = (
  start: PixelPoint,
  end: PixelPoint,
  page: PixelSize,
  minimumSizePixels = 8
): NormalizedBox | null => {
  requirePageSize(page);
  if (![start.x, start.y, end.x, end.y, minimumSizePixels].every(Number.isFinite)) {
    throw new Error("draw coordinates must be finite");
  }
  const left = clamp(Math.min(start.x, end.x), 0, page.width);
  const top = clamp(Math.min(start.y, end.y), 0, page.height);
  const right = clamp(Math.max(start.x, end.x), 0, page.width);
  const bottom = clamp(Math.max(start.y, end.y), 0, page.height);
  if (right - left < minimumSizePixels || bottom - top < minimumSizePixels) {
    return null;
  }
  return pixelsToNormalized(
    {x: left, y: top, width: right - left, height: bottom - top},
    page
  );
};

export const updateFragmentGeometry = (
  fragment: QuestionFrameFragment,
  geometry: NormalizedBox
): QuestionFrameFragment => {
  requireFiniteRect(geometry);
  if (geometry.x < 0 || geometry.y < 0 || geometry.x + geometry.width > 1 || geometry.y + geometry.height > 1) {
    throw new Error("fragment geometry must stay within normalized page bounds");
  }
  return {...fragment, ...geometry};
};
