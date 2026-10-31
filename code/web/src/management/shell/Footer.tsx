import type { MerchantOverview } from "../../types.ts";
import { Seed } from "../ui/index.ts";

/** Under every page but Assistant. The demo line only for a merchant built from a demo site. */
export function Footer({ merchant }: { merchant: MerchantOverview | null }) {
  const demo = merchant?.demo === true;
  return (
    <footer className="foot">
      {demo && <span>Demo data. {merchant.name} is a fictional restaurant.</span>}
      <span className={demo ? undefined : "foot-run"}>
        Run on{" "}
        <span className="logo" role="img" aria-label="Sésame">
          <span aria-hidden="true">
            S
            <span className="acc">
              e<Seed />
            </span>
            same
          </span>
        </span>
      </span>
    </footer>
  );
}
