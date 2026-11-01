#!/bin/sh
# The visual tests (e2e/visual.spec.ts, #153) in Playwright's own image, the one CI runs them
# in: fonts render differently from one system to another, so the baselines are taken there.
#   npm run test:visual:docker                          check against the baselines
#   npm run test:visual:docker -- --update-snapshots    take them again
set -eu
cd "$(dirname "$0")/../.."
version=$(node -p 'require("./web/node_modules/@playwright/test/package.json").version')
# code/ is mounted for the fixtures the web app imports; node_modules stays the image's own.
exec docker run --rm --ipc=host -v "$PWD:/code" -v /code/web/node_modules -w /code/web \
  "mcr.microsoft.com/playwright:v$version-noble" \
  sh -c 'npm ci --no-audit --no-fund --loglevel=error && npm run test:visual -- "$@"' sh "$@"
