// Geometry of Home's guests chart (sesame-management.html, drawChart): pure, so the
// tick and label positions are tested without a browser.

export const CHART_HEIGHT = 216;
export const CHART_MIN_WIDTH = 280;
export const MARGIN = { left: 30, right: 6, top: 10, bottom: 26 } as const;
// Days labelled under the chart; the last one is always the newest day.
const LABELLED = [0, 7, 14, 21] as const;

export type Tick = { value: number; y: number };
export type XLabel = { index: number; x: number; anchor: "start" | "middle" | "end" };

export type ChartGeometry = {
  width: number;
  height: number;
  // The highest tick: the largest day rounded up to 20s, at least 20.
  top: number;
  ticks: Tick[];
  points: { x: number; y: number }[];
  labels: XLabel[];
  baseline: number;
  x: (index: number) => number;
  y: (value: number) => number;
  // The day under a pointer at `px` (chart units).
  indexAt: (px: number) => number;
};

export function chartGeometry(values: readonly number[], measuredWidth: number): ChartGeometry {
  const width = Math.max(CHART_MIN_WIDTH, measuredWidth);
  const height = CHART_HEIGHT;
  const { left, right, top: marginTop, bottom } = MARGIN;
  const last = Math.max(1, values.length - 1);
  const step = (width - left - right) / last;
  const top = Math.max(20, Math.ceil(Math.max(0, ...values) / 20) * 20);
  const x = (index: number) => left + index * step;
  const y = (value: number) => marginTop + (1 - value / top) * (height - marginTop - bottom);
  const lastIndex = values.length - 1;
  const labelled = [...LABELLED.filter((index) => index < lastIndex), lastIndex].filter(
    (index) => index >= 0,
  );
  return {
    width,
    height,
    top,
    ticks: [0, top / 2, top].map((value) => ({ value, y: y(value) })),
    points: values.map((value, index) => ({ x: x(index), y: y(value) })),
    labels: labelled.map((index) => ({
      index,
      x: x(index),
      anchor: index === 0 ? "start" : index === lastIndex ? "end" : "middle",
    })),
    baseline: y(0),
    x,
    y,
    indexAt: (px) => Math.max(0, Math.min(lastIndex, Math.round((px - left) / step))),
  };
}

/** The area under the line and the line itself, as SVG path data. */
export function chartPaths(geometry: ChartGeometry): { area: string; line: string } {
  const { points, baseline } = geometry;
  if (points.length === 0) return { area: "", line: "" };
  const joined = points.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join("L");
  const first = points[0].x;
  const end = points[points.length - 1].x;
  return {
    area: `M${first} ${baseline}L${joined}L${end} ${baseline}Z`,
    line: `M${joined}`,
  };
}
