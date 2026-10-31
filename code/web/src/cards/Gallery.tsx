import type { ReactNode } from "react";
import type { CardEvent, StepGroup } from "../chat/state.ts";
import cards from "../mocks/cards.json";
import notification from "../mocks/notification.json";
import onboarding from "../mocks/onboarding.json";
import tonight from "../mocks/tonight.json";
import type { ChatEvent, LiveEvent } from "../types.ts";
import { CardView } from "./CardView.tsx";
import { ErrorItem } from "./ErrorItem.tsx";
import { NotificationItem } from "./NotificationItem.tsx";
import { StepsCard } from "./StepsCard.tsx";

// Every card and state from the contract mocks, for visual review (/?gallery).
const EVENTS = [...onboarding, ...tonight, ...cards] as ChatEvent[];
const CARDS = EVENTS.filter((event): event is CardEvent => event.type === "card");
const NOTIFICATION = notification as LiveEvent;

const DONE_STEPS: StepGroup["steps"] = Object.fromEntries(
  EVENTS.flatMap((event) =>
    event.type === "step" && event.status === "done"
      ? [[event.step, { status: event.status, detail: event.detail, duration_ms: event.duration_ms }]]
      : [],
  ),
);

const RUNNING_STEPS: StepGroup["steps"] = {
  crawl: DONE_STEPS.crawl,
  extract: { status: "running" },
};

const FAILED_STEPS: StepGroup["steps"] = {
  crawl: DONE_STEPS.crawl,
  extract: DONE_STEPS.extract,
  validate: {
    status: "error",
    detail: "I couldn't find your opening hours. Add them to your description.",
  },
};

const reservations = CARDS.find((card) => card.kind === "reservations");

export function Gallery() {
  return (
    <div className="messages">
      <div className="messages-inner">
        <h1>Chat cards</h1>
        <Section title="Steps">
          <StepsCard steps={RUNNING_STEPS} />
          <StepsCard steps={DONE_STEPS} />
          <StepsCard steps={FAILED_STEPS} />
        </Section>
        <Section title="Cards">
          {CARDS.map((card) => (
            <CardView key={card.kind} card={card} />
          ))}
        </Section>
        {reservations && (
          <Section title="Reservations: cancelled row and empty state">
            <CardView
              card={{
                ...reservations,
                data: {
                  ...reservations.data,
                  reservations: [
                    ...reservations.data.reservations,
                    {
                      id: "R-003",
                      time: "21:30",
                      guest_name: "Kim Weber",
                      party_size: 3,
                      table_label: "T6",
                      via: "merchant",
                      status: "cancelled",
                    },
                  ],
                },
              }}
            />
            <CardView card={{ ...reservations, data: { ...reservations.data, reservations: [] } }} />
          </Section>
        )}
        <Section title="Notifications">
          <NotificationItem event={NOTIFICATION} />
          <NotificationItem event={{ ...NOTIFICATION, kind: "updated" }} />
          <NotificationItem event={{ ...NOTIFICATION, kind: "cancelled", via: "website" }} />
        </Section>
        <Section title="Errors">
          <ErrorItem message="I can't reach the platform right now." onRetry={() => {}} />
          <ErrorItem message="The platform couldn't answer this message." />
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <h2 className="muted">{title}</h2>
      {children}
    </>
  );
}
