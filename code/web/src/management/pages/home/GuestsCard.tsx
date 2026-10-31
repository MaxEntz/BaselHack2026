import { type PointerEvent, useEffect, useRef, useState } from "react";
import type { DayHistory } from "../../../types.ts";
import { Box, Pill } from "../../ui/index.ts";
import { MARGIN, chartGeometry, chartPaths } from "./chart.ts";
import { count, dayMonth, dayShort, guests } from "./format.ts";

type View = "chart" | "table";

export const NO_HISTORY = "Your guests appear here after their first booking.";

/** Guests over the last 30 days: totals, then a chart or a table. */
export function GuestsCard({ history }: { history: DayHistory[] }) {
  const [view, setView] = useState<View>("chart");
  const total = history.reduce((sum, day) => sum + day.guests, 0);
  const agent = history.reduce((sum, day) => sum + day.agent_guests, 0);
  const cancelled = history.reduce((sum, day) => sum + day.cancelled, 0);
  const empty = history.every((day) => day.bookings === 0 && day.guests === 0 && day.cancelled === 0);
  return (
    <Box tone="olive" className="m s7" aria-labelledby="home-guests">
      <div className="m-hd">
        <h2 id="home-guests">Guests</h2>
        {!empty && (
          <div className="pills" role="group" aria-label="View">
            <Pill small aria-pressed={view === "chart"} onClick={() => setView("chart")}>
              Chart
            </Pill>
            <Pill small aria-pressed={view === "table"} onClick={() => setView("table")}>
              Table
            </Pill>
          </div>
        )}
      </div>
      <div className="big">
        <b className="num">{count(total)}</b>
        <span>guests in the last 30 days</span>
      </div>
      <div className="stats">
        <span>
          <b className="num">{count(agent)}</b> through your agent
        </span>
        <span>
          <b className="num">{count(cancelled)}</b> cancelled
        </span>
      </div>
      {empty ? (
        <p className="home-empty">{NO_HISTORY}</p>
      ) : view === "chart" ? (
        <GuestsChart history={history} />
      ) : (
        <GuestsTable history={history} />
      )}
    </Box>
  );
}

function GuestsTable({ history }: { history: DayHistory[] }) {
  return (
    <div className="hist">
      <table>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Guests</th>
            <th scope="col">Agent</th>
            <th scope="col">Cancelled</th>
          </tr>
        </thead>
        <tbody>
          {[...history].reverse().map((day) => (
            <tr key={day.date}>
              <td>{dayShort(day.date)}</td>
              <td>{day.guests}</td>
              <td>{day.agent_guests}</td>
              <td>{day.cancelled}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    setWidth(node.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function GuestsChart({ history }: { history: DayHistory[] }) {
  const [box, measured] = useWidth<HTMLDivElement>();
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ index: number; left: number } | null>(null);
  const geometry = chartGeometry(
    history.map((day) => day.guests),
    measured,
  );
  const { width, height, ticks, labels, baseline, x, y } = geometry;
  const { area, line } = chartPaths(geometry);
  const last = history.length - 1;

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const index = geometry.indexAt(((event.clientX - rect.left) * width) / rect.width);
    const left = Math.max(70, Math.min(rect.width - 70, (x(index) * rect.width) / width));
    setHover({ index, left });
  }

  const shown = hover ? history[hover.index] : null;
  return (
    <div className="chart" ref={box}>
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${height}`}
        height={height}
        role="img"
        aria-label="Guests per day over the last 30 days"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((tick) => (
          <g key={tick.value} data-tick={tick.value}>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={tick.y} y2={tick.y} stroke="#f4f2ea" strokeOpacity=".28" />
            <text x={MARGIN.left - 7} y={tick.y + 5} textAnchor="end">
              {tick.value}
            </text>
          </g>
        ))}
        <path d={area} fill="#f4f2ea" fillOpacity=".16" />
        <path d={line} fill="none" stroke="#f4f2ea" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {last >= 0 && <circle cx={x(last)} cy={y(history[last].guests)} r="5" fill="#f4f2ea" />}
        {labels.map((label) => (
          <text key={label.index} x={label.x} y={height - 6} textAnchor={label.anchor} data-label={label.index}>
            {dayMonth(history[label.index].date)}
          </text>
        ))}
        {hover && shown && (
          <>
            <line x1={x(hover.index)} x2={x(hover.index)} y1={MARGIN.top} y2={baseline} stroke="#f4f2ea" strokeOpacity=".6" />
            <circle cx={x(hover.index)} cy={y(shown.guests)} r="5" fill="#f4f2ea" />
          </>
        )}
      </svg>
      {hover && shown && (
        <div className="tip num" style={{ left: `${hover.left}px`, top: `${y(shown.guests)}px` }}>
          {dayShort(shown.date)}, {guests(shown.guests)}
        </div>
      )}
    </div>
  );
}
