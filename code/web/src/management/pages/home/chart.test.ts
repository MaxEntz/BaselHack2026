import { describe, expect, it } from "vitest";
import { CHART_HEIGHT, MARGIN, chartGeometry, chartPaths } from "./chart.ts";

const thirty = (top: number) => Array.from({ length: 30 }, (_, i) => (i === 29 ? top : i % 5));

describe("guests chart geometry", () => {
  it("rounds the top up to 20s and puts ticks at 0, half and top", () => {
    const g = chartGeometry(thirty(39), 600);
    expect(g.top).toBe(40);
    expect(g.ticks.map((t) => t.value)).toEqual([0, 20, 40]);
    const plot = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;
    expect(g.ticks.map((t) => t.y)).toEqual([MARGIN.top + plot, MARGIN.top + plot / 2, MARGIN.top]);
    expect(g.baseline).toBe(CHART_HEIGHT - MARGIN.bottom);
  });

  it("keeps a top of 20 when every day is empty", () => {
    expect(chartGeometry(Array(30).fill(0), 600).top).toBe(20);
    expect(chartGeometry(thirty(40), 600).top).toBe(40);
    expect(chartGeometry(thirty(41), 600).top).toBe(60);
  });

  it("labels days 0, 7, 14, 21 and the last, anchored start, middle and end", () => {
    const g = chartGeometry(thirty(39), 600);
    expect(g.labels.map((l) => l.index)).toEqual([0, 7, 14, 21, 29]);
    expect(g.labels.map((l) => l.anchor)).toEqual(["start", "middle", "middle", "middle", "end"]);
    const step = (600 - MARGIN.left - MARGIN.right) / 29;
    expect(g.labels[0].x).toBe(MARGIN.left);
    expect(g.labels[2].x).toBeCloseTo(MARGIN.left + 14 * step);
    expect(g.labels[4].x).toBeCloseTo(600 - MARGIN.right);
  });

  it("is never narrower than 280", () => {
    expect(chartGeometry(thirty(39), 0).width).toBe(280);
    expect(chartGeometry(thirty(39), 900).width).toBe(900);
  });

  it("finds the day under the pointer, clamped to the series", () => {
    const g = chartGeometry(thirty(39), 600);
    expect(g.indexAt(0)).toBe(0);
    expect(g.indexAt(g.x(12) + 3)).toBe(12);
    expect(g.indexAt(10_000)).toBe(29);
  });

  it("closes the area on the baseline", () => {
    const g = chartGeometry([0, 20, 40], 300);
    const { area, line } = chartPaths(g);
    expect(line.startsWith(`M${g.x(0).toFixed(1)} ${g.baseline.toFixed(1)}`)).toBe(true);
    expect(area.endsWith(`L${g.x(2)} ${g.baseline}Z`)).toBe(true);
  });
});
