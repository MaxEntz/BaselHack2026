import type { CardEvent } from "../chat/state.ts";
import { ConfirmationCard, DiffCard, PolicyCard } from "./PolicyCard.tsx";
import { ProfileCard } from "./ProfileCard.tsx";
import { ReservationsCard } from "./ReservationsCard.tsx";
import { StatsCard } from "./StatsCard.tsx";

export function CardView({ card }: { card: CardEvent }) {
  switch (card.kind) {
    case "profile":
      return <ProfileCard data={card.data} loginUrl={card.login_url ?? null} />;
    case "reservations":
      return <ReservationsCard data={card.data} />;
    case "stats":
      return <StatsCard data={card.data} />;
    case "policy":
      return <PolicyCard data={card.data} />;
    case "confirmation":
      return <ConfirmationCard data={card.data} />;
    case "diff":
      return <DiffCard data={card.data} />;
    case "view":
    case "page":
      return null;
  }
}
