import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const directory = await mkdtemp(join(tmpdir(), "merchant-contracts-"));
const types = fileURLToPath(new URL("../src/types.ts", import.meta.url));
const mocks = {
  onboarding: "ChatEvent[]",
  tonight: "ChatEvent[]",
  cards: "ChatEvent[]",
  notification: "LiveEvent",
  session: "SessionOutput",
  // The management mock's data (#129, #131).
  "../../../tests/fixtures/demo/la-graine-verte-management": "ManagementFixture",
  // The assistant's opening conversation (#131, #140).
  "../../../tests/fixtures/demo/la-graine-verte-assistant": "{ merchant: string; opening: ConversationItem[] }",
};

try {
  const files = [];
  for (const [name, type] of Object.entries(mocks)) {
    const source = await readFile(
      new URL(`../src/mocks/${name}.json`, import.meta.url),
      "utf8",
    );
    const data = JSON.stringify(JSON.parse(source), null, 2);
    const file = join(directory, `${name.split("/").at(-1)}.ts`);
    // JSON imports widen string literals, so check the actual JSON as TS literals.
    await writeFile(
      file,
      `import type { ChatEvent, ConversationItem, LiveEvent, ManagementFixture, SessionOutput } from ${JSON.stringify(types)};\n` +
        `export default ${data} satisfies ${type};\n`,
    );
    files.push(file);
  }

  const config = join(directory, "tsconfig.json");
  await writeFile(config, JSON.stringify({
    compilerOptions: {
      strict: true,
      noEmit: true,
      target: "ES2022",
      module: "ESNext",
      moduleResolution: "bundler",
      allowImportingTsExtensions: true,
      types: [],
    },
    files,
  }));
  const compiler = fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url));
  const result = spawnSync(process.execPath, [compiler, "--project", config], { stdio: "inherit" });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} finally {
  await rm(directory, { recursive: true, force: true });
}
