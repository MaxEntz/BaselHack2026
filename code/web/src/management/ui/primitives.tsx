import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { BLOB_PATHS, FORK_PATHS, ICON_PATHS, type IconName, SEED_PATH, blobNumber } from "./shapes.ts";

function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

type PillLook = { primary?: boolean; small?: boolean };

/** Rounded button: `primary` is brick with a shadow, `small` is the compact size. */
export function Pill({ primary, small, className, type = "button", ...rest }: PillLook & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={cx("pill", primary && "pri", small && "sm", className)} {...rest} />;
}

export function Tag({ className, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cx("tag", className)} {...rest} />;
}

type BoxTone = "olive" | "brick" | "cream" | "plain";

/** Rounded panel. Olive and brick are dark: Pills and focus rings inside turn light. */
export function Box({ tone = "plain", className, ...rest }: { tone?: BoxTone } & HTMLAttributes<HTMLElement>) {
  const dark = tone === "olive" || tone === "brick";
  return <section className={cx("box", tone !== "plain" && tone, dark && "on-dark", className)} {...rest} />;
}

/** Organic shape behind a time or an avatar: b1-b4 by `(index % 4) + 1`. Fill with `--fill`. */
export function Blob({ index, className }: { index: number; className?: string }) {
  const n = blobNumber(index);
  return (
    <svg className={cx("blob", className)} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" data-blob={`b${n}`}>
      <path vectorEffect="non-scaling-stroke" d={BLOB_PATHS[n - 1]} />
    </svg>
  );
}

/** Underlines its text with the hand-drawn brick stroke. */
export function Under({ children }: { children: ReactNode }) {
  return <span className="under">{children}</span>;
}

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={cx("ico", className)} viewBox="0 0 24 24" aria-hidden="true">
      {ICON_PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

/** The fork-and-knife doodle (d-fork). */
export function Doodle({ className }: { className?: string }) {
  return (
    <svg className={cx("doodle", className)} viewBox="0 0 120 120" aria-hidden="true">
      <g transform="rotate(28 60 60)">
        {FORK_PATHS.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
    </svg>
  );
}

/** The seed mark. */
export function Seed({ className }: { className?: string }) {
  return (
    <svg className={cx("seed", className)} viewBox="0 0 44 60" aria-hidden="true">
      <path d={SEED_PATH} />
    </svg>
  );
}
