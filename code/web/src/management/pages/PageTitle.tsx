import "../forms.css";
import type { ReactNode } from "react";
import { Under } from "../ui/index.ts";

export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="ttl">
      <h1>
        <Under>{title}</Under>
      </h1>
      {children && <p>{children}</p>}
    </div>
  );
}
