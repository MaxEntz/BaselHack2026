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
  const failed = STEPS.some(({ name }) => steps[name]?.status === "error");
  const finished = STEPS.every(({ name }) => steps[name]?.status === "done");
  const total = STEPS.reduce(
    (sum, { name }) => sum + (steps[name]?.duration_ms ?? 0),
    0,
  );
  const title = failed
    ? "The build stopped"
    : finished
      ? `Agent built in ${formatDuration(total)}`
      : "Building your agent";

  return (
    <section className="card steps-card" aria-label="Agent build progress">
      <h2 className="card-title">{title}</h2>
      <ol className="steps">
        {STEPS.map(({ name, label }) => {
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
