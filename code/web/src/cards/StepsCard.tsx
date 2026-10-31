import type { StepGroup, StepName } from "../chat/state.ts";
import { formatDuration } from "./format.ts";

const STEPS: readonly { name: StepName; label: string }[] = [
  { name: "crawl", label: "Reading your website" },
  { name: "extract", label: "Understanding your restaurant" },
  { name: "validate", label: "Checking the profile" },
  { name: "provision", label: "Creating your agent" },
  { name: "deploy", label: "Going live" },
];

export function StepsCard({ steps }: Pick<StepGroup, "steps">) {
  // A build waits for its owner before `deploy`; publishing streams `deploy` alone (#136).
  const publishing = Object.keys(steps).length === 1 && steps.deploy !== undefined;
  const shown = publishing ? STEPS.filter(({ name }) => name === "deploy") : STEPS;
  const failed = shown.some(({ name }) => steps[name]?.status === "error");
  const finished = shown.every(({ name }) => steps[name]?.status === "done");
  const ready = steps.deploy?.status === "waiting";
  const total = shown.reduce(
    (sum, { name }) => sum + (steps[name]?.duration_ms ?? 0),
    0,
  );
  const title = publishing
    ? failed
      ? "Publishing stopped"
      : finished
        ? "Your agent is live"
        : "Publishing your agent"
    : failed
      ? "The build stopped"
      : finished
        ? `Agent built in ${formatDuration(total)}`
        : ready
          ? `Agent built in ${formatDuration(total)}: check it, then publish`
          : "Building your agent";

  return (
    <section className="card steps-card" aria-label="Agent build progress">
      <h2 className="card-title">{title}</h2>
      <ol className="steps">
        {shown.map(({ name, label }) => {
          const step = steps[name];
          const status = step?.status ?? "waiting";
          return (
            <li key={name} className={`step step-${status}`}>
              <span className="step-icon" aria-hidden="true">
                {status === "running" && <span className="spinner" />}
                {status === "done" && "✓"}
                {status === "error" && "✗"}
              </span>
              <span className="step-label">{label}</span>
              {status === "done" && step?.duration_ms != null && (
                <span className="step-duration">
                  {formatDuration(step.duration_ms)}
                </span>
              )}
              {step?.detail && <p className="step-detail">{step.detail}</p>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
