#!/usr/bin/env node
// Thin launcher for the Poliscope Agent Skill and its Python CLI, so both can
// be used with `npx github:Fishman-free/poliscope` -- no clone, no JSON
// manifest, no Python environment of the user's own.
//
// Two responsibilities:
//
//   1. `install-skill` -- copy the bundled Agent Skill into the user's agent
//      skills directory. Pure file copying, so it lives here rather than in
//      the Python CLI, and it is the one subcommand that works with no Python
//      at all.
//
//   2. Everything else -- delegate to the Python CLI.
//      Resolution order:
//        a. $POLISCOPE_PYTHON -- explicit interpreter override.
//        b. The repository's own .venv (when running from a checkout).
//        c. `uvx` from the Git source -- zero-install, needs uv
//           (https://docs.astral.sh/uv/), the same path the SKILL.md documents.
//
// Anything else is a clear error telling the user what is missing; a failed
// Python launch is never masked into a fake success.

import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ARGS = process.argv.slice(2);
const GIT_SOURCE = "git+https://github.com/Fishman-free/poliscope.git";
const SKILL_NAME = "poliscope";

const USAGE_INSTALL = `poliscope install-skill [--dir <path>] [--force]

Copy the Poliscope Agent Skill into a skills directory, so Claude Code or
Codex can invoke it as /poliscope.

  --dir <path>   Install into <path>/${SKILL_NAME} instead of the default.
                 Defaults to $CLAUDE_SKILLS_DIR, else ~/.claude/skills.
  --force        Overwrite an existing installation.

After installing, restart your agent and type /poliscope.`;

function installSkill() {
  const rest = ARGS.slice(1);
  if (rest.includes("-h") || rest.includes("--help")) {
    console.log(USAGE_INSTALL);
    process.exit(0);
  }

  let baseDir =
    process.env.CLAUDE_SKILLS_DIR || join(homedir(), ".claude", "skills");
  let force = false;
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i] === "--dir") {
      const value = rest[i + 1];
      if (!value) {
        console.error("poliscope: --dir needs a path");
        process.exit(1);
      }
      baseDir = value;
      i += 1;
    } else if (rest[i] === "--force") {
      force = true;
    } else {
      console.error(`poliscope: unknown option for install-skill: ${rest[i]}`);
      console.error(USAGE_INSTALL);
      process.exit(1);
    }
  }

  // The skill ships inside the package (package.json "files" includes
  // "skills"), so this behaves the same from npx, from a git checkout, and
  // from a global install.
  const source = join(ROOT, "skills", SKILL_NAME);
  if (!existsSync(join(source, "SKILL.md"))) {
    console.error(
      `poliscope: could not find the bundled skill at ${source}.\n` +
        "Install from the Git source instead: " +
        "`npx github:Fishman-free/poliscope install-skill`.",
    );
    process.exit(1);
  }

  const target = resolve(baseDir, SKILL_NAME);
  if (existsSync(target) && !force) {
    console.error(
      `poliscope: ${target} already exists. Re-run with --force to overwrite.`,
    );
    process.exit(1);
  }

  mkdirSync(baseDir, { recursive: true });
  if (force) rmSync(target, { recursive: true, force: true });
  cpSync(source, target, { recursive: true });
  console.log(`poliscope: skill installed to ${target}`);
  console.log("Restart your agent and type /poliscope");
}

if (ARGS[0] === "install-skill") {
  installSkill();
  process.exit(0);
}

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
