import { type InputHTMLAttributes, type ReactNode, useId } from "react";

/** A labelled input with an optional hint. Pass `children` for a select or a textarea. */
export function Field({
  label,
  hint,
  children,
  ...input
}: { label: ReactNode; hint?: ReactNode; children?: ReactNode } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <label className="field" htmlFor={children ? undefined : id}>
      {label}
      {children ?? <input id={id} type="text" {...input} />}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Checkbox({ label, ...input }: { label: ReactNode } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label>
      <input type="checkbox" {...input} />
      {label}
    </label>
  );
}
