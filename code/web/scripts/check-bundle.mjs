// Invariant 19 (documentation/ARCHITECTURE.md, #220): a production build ships no mock, no demo
// fixture, no demo restaurant and no review gallery. Run after `npm run build`; it builds nothing.
//   npm run check:bundle               checks dist/
//   node scripts/check-bundle.mjs DIR  checks another build (a mock build must fail)
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const web = fileURLToPath(new URL("..", import.meta.url));
const dist = join(process.argv[2] ?? join(web, "dist"), "assets");

// A text that only this source holds, as it reaches the minified JS. Each is checked in its
// source first, so that a reworded mock can't leave a marker that matches nothing.
const MARKERS = [
  ["src/mocks/mockClient.ts", "Thanks! Now describe how you work"],
  ["src/mocks/management/bookings.ts", "No booking with that reference"],
  ["src/mocks/onboarding.json", "Read 1 page: /"],
  ["src/mocks/tonight.json", "You have 2 bookings for 6 guests tonight"],
  ["src/mocks/cards.json", "Checking tonight's numbers"],
  ["../tests/fixtures/demo/la-graine-verte-management.json", "Lena Becker"],
  ["../tests/fixtures/demo/la-graine-verte-assistant.json", "**20 guests** in 9 bookings"],
  ["../tests/fixtures/le-petit-zinc.json", "localhost:8080/reservation.html"],
  ["../tests/fixtures/maison-albane.json", "Free cancellation up to 24 hours before the booking."],
  ["src/studio/examples.ts", "lepetitzinc.example"],
  ["src/studio/examples.ts", "maisonalbane.example"],
  ["src/cards/Gallery.tsx", "Chat cards"],
  ["src/management/PrimitivesGallery.tsx", "mg mg-gallery"],
];

const problems = [];
for (const [source, marker] of MARKERS) {
  const text = await readFile(join(web, source), "utf8");
  if (!text.includes(marker)) problems.push(`${source} no longer holds "${marker}": pick another marker`);
}

let files;
try {
  files = await readdir(dist);
} catch {
  console.error(`No build in ${dist}: run \`npm run build\` first.`);
  process.exit(1);
}
const scripts = files.filter((file) => file.endsWith(".js"));
if (scripts.length === 0) problems.push(`no JavaScript in ${dist}: run \`npm run build\` first`);
for (const file of files.filter((name) => name.endsWith(".jpg"))) {
  problems.push(`${file}: the mock's site image (src/mocks/site.ts) is emitted`);
}
for (const file of scripts) {
  const code = await readFile(join(dist, file), "utf8");
  for (const [source, marker] of MARKERS) {
    if (code.includes(marker)) problems.push(`${file} holds "${marker}", from ${source}`);
  }
}

if (problems.length > 0) {
  console.error(`The build ships what only the mock may (#220):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`${scripts.length} script(s) in ${dist}: no mock, fixture, demo restaurant or gallery.`);
