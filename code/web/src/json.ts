// Narrowing JSON the platform sent (#220): what `JSON.parse` or `response.json()` gives is
// `unknown` until checked, never cast straight to a contract type.

/** A JSON object: not null, not an array. */
export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * Every `type` of the union `T`, as a set. Built from a record with one key per type, which `tsc`
 * holds to the union: a type added to `T` and missing here, or one here that `T` doesn't have,
 * fails the build. `of` only carries `T`, so that a reader can't be given another union's types.
 */
export type TypesOf<T extends { type: string }> = { readonly types: ReadonlySet<string>; readonly of?: T };

export function typesOf<T extends { type: string }>(every: Record<T["type"], true>): TypesOf<T> {
  return { types: new Set(Object.keys(every)) };
}
