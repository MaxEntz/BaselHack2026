// SVG shapes copied from the mockup's sprite (sesame-management.html, lines 384-397).

/** The four organic blob outlines (b1-b4), viewBox 0 0 100 100. */
export const BLOB_PATHS = [
  "M9 12C22 3 36 7 50 6 66 4 82 0 92 10 100 21 96 36 97 50 98 66 102 84 91 92 80 100 64 95 50 96 34 97 18 102 7 91-1 80 4 64 3 50 2 35-2 21 9 12Z",
  "M14 5C30 11 40 2 56 3 72 4 90 1 96 16 100 30 92 40 96 54 100 70 101 86 87 94 72 100 62 91 47 95 31 99 13 101 5 86-1 71 8 60 4 45 1 31 1 11 14 5Z",
  "M6 26C7 11 21 2 37 5 50 8 60 1 75 3 91 5 100 18 96 34 93 46 99 56 97 70 94 87 79 98 63 95 51 92 43 99 28 97 12 95 1 82 4 66 6 53 2 42 6 26Z",
  "M18 8C31 1 44 8 57 5 73 2 88 5 95 20 100 33 93 44 95 58 97 74 95 90 79 95 66 99 55 92 41 95 25 98 8 94 3 78-1 65 8 54 5 40 2 27 5 15 18 8Z",
] as const;

/** The blob of the index-th item: b1-b4, assigned by `(index % 4) + 1`. Returns 1 to 4. */
export function blobNumber(index: number): 1 | 2 | 3 | 4 {
  return ((((index % 4) + 4) % 4) + 1) as 1 | 2 | 3 | 4;
}

/** Icons, viewBox 24, stroked (see `.ico`). The search circle is written as two arcs. */
export const ICON_PATHS = {
  left: ["M15 5l-7 7 7 7"],
  right: ["M9 5l7 7-7 7"],
  x: ["M6 6l12 12M18 6L6 18"],
  search: ["M4 10.5a6.5 6.5 0 1 0 13 0a6.5 6.5 0 1 0-13 0", "M15.5 15.5L21 21"],
  bell: ["M6 16c1-2 1-4 1-6a5 5 0 0 1 10 0c0 2 0 4 1 6l1 2H5ZM10 21c1 1 3 1 4 0"],
  up: ["M12 19V5M6 11l6-6 6 6"],
  expand: ["M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"],
  shrink: ["M20 10h-6V4M14 10l6-6M4 14h6v6M10 14l-6 6"],
} as const;

export type IconName = keyof typeof ICON_PATHS;

export const SEED_PATH = "M3 60C-3 38 8 8 30 1 43-3 47 10 43 22 37 40 21 53 3 60Z";

/** The fork-and-knife doodle (d-fork), drawn rotated by 28° around (60, 60). */
export const FORK_PATHS = [
  "M26 8V32C26 44 34 48 38 48V108C38 113 44 113 44 108V48C48 48 56 44 56 32V8M36 8V30M46 8V30",
  "M84 8C97 20 99 46 93 64H85V108C85 113 79 113 79 108V13C79 8 81 6 84 8Z",
] as const;
