export type PageViewMode = "fit-page" | "fit-width" | "actual";

export interface PageViewportInput {
  pageWidth: number;
  pageHeight: number;
  viewportWidth: number;
  viewportHeight: number;
  mode: PageViewMode;
  zoom: number;
  padding?: number;
}

export interface PageViewportResult {
  scale: number;
  width: number;
  height: number;
  overflowX: boolean;
  overflowY: boolean;
}

export function clampPageZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.max(.25, Math.min(3, value));
}

export function calculatePageViewport(input: PageViewportInput): PageViewportResult {
  const pageWidth = Number.isFinite(input.pageWidth) && input.pageWidth > 0 ? input.pageWidth : 1;
  const pageHeight = Number.isFinite(input.pageHeight) && input.pageHeight > 0 ? input.pageHeight : 1;
  const padding = Number.isFinite(input.padding) ? Math.max(0, input.padding ?? 0) : 0;
  const availableWidth = Math.max(1, input.viewportWidth - padding);
  const availableHeight = Math.max(1, input.viewportHeight - padding);
  const zoom = clampPageZoom(input.zoom);

  // Fit-page must obey both dimensions; using width alone recreates the long
  // vertical canvas and competing scrollbars that made accidental sliding common.
  const baseScale = input.mode === "actual"
    ? 1
    : input.mode === "fit-width"
      ? availableWidth / pageWidth
      : Math.min(availableWidth / pageWidth, availableHeight / pageHeight);
  const scale = Math.max(.01, Math.min(3, baseScale * zoom));
  const width = pageWidth * scale;
  const height = pageHeight * scale;
  return {
    scale,
    width,
    height,
    overflowX: width > availableWidth + .5,
    overflowY: height > availableHeight + .5
  };
}
