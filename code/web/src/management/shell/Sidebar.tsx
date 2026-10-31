import { Link } from "react-router";
import type { MerchantOverview } from "../../types.ts";
import { PAGES, type PageId, pagePath } from "../pages.ts";
import { Doodle } from "../ui/index.ts";
import { brandLine } from "./format.ts";

// The sidebar's order (#137): the evening's pages, then the restaurant's.
const MAIN: readonly PageId[] = ["home", "assistant", "reservations", "guests"];
const RESTAURANT: readonly PageId[] = ["hours", "floor", "rules", "agent"];

export function Sidebar({
  slug,
  page,
  name,
  merchant,
}: {
  slug: string;
  page: PageId;
  name: string;
  merchant: MerchantOverview | null;
}) {
  const link = (id: PageId) => (
    <Link key={id} to={pagePath(slug, id)} aria-current={id === page ? "page" : undefined} viewTransition>
      {PAGES[id]}
    </Link>
  );
  return (
    <aside className="side">
      <div>
        <Link className="brand" to={pagePath(slug, "home")} viewTransition>
          {name}
        </Link>
        {merchant && <span className="brand-sub">{brandLine(merchant.city, merchant.price_level)}</span>}
      </div>
      <div className="side-wrap">
        <nav aria-label="Main">
          {MAIN.map(link)}
          <h3>Restaurant</h3>
          {RESTAURANT.map(link)}
        </nav>
        <div className="side-foot">
          <Doodle className="side-doodle" />
          <nav aria-label="Account" style={{ display: "contents" }}>
            {link("settings")}
          </nav>
        </div>
      </div>
    </aside>
  );
}
