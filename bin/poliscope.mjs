#!/usr/bin/env node
// Thin launcher for the Python `poliscope` CLI, so it can be run with
// `npx poliscope` / `npx github:Fishman-free/poliscope` without a Python
// environment of the user's own.
//
// Resolution order:
//   1. $POLISCOPE_PYTHON -- explicit interpreter override.
//   2. The repository's own .venv (when running from a checkout).
//   3. `uvx` from the Git source -- zero-install, needs uv
//      (https://docs.astral.sh/uv/), the same path the SKILL.md documents.
//
// Everything else is a clear error telling the user what is missing; a
// failed Python launch is never masked into a fake success.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ARGS = process.argv.slice(2);
const GIT_SOURCE = "git+https://github.com/Fishman-free/poliscope.git";

function run(command, commandArgs) {
  const result = spawnSync(command, commandArgs, { stdio: "inherit" });
  if (result.error) return false;
  process.exit(result.status ?? 1);
  return true;
}

if (process.env.POLISCOPE_PYTHON) {
  run(process.env.POLISCOPE_PYTHON, ["-m", "apps.cli.main", ...ARGS]);
}

const venvPython =
  process.platform === "win32"
    ? join(ROOT, ".venv", "Scripts", "python.exe")
    : join(ROOT, ".venv", "bin", "python");
if (existsSync(venvPython)) {
  run(venvPython, ["-m", "apps.cli.main", ...ARGS]);
}

run("uvx", [
  "--from",
  GIT_SOURCE,
  "poliscope",
  ...ARGS,
]);

console.error(
  "poliscope: could not start the CLI. Install uv (https://docs.astral.sh/uv/) " +
    "so `uvx` can fetch it, or point $POLISCOPE_PYTHON at a Python 3.12+ with " +
    "the project installed.",
);
process.exit(1);
