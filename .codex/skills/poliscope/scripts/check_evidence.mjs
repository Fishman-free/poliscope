#!/usr/bin/env node
// Mechanical half of the Poliscope evidence gate.
//
// This checks the things that can be decided by looking at evidence.json:
// dangling ids, findings with no anchor, a correlation published as a cause,
// a refuted claim published as settled, quarantined items stripped of their
// reason, cluster counts that contradict the sources.
//
// It cannot check whether a locator is honest, whether a quote says what the
// finding claims, or whether the reasoning is any good. A green run is not a
// claim that the research is correct -- see references/evidence-gate.md.
//
// Usage:  node check_evidence.mjs <evidence.json | directory>
// Exit:   0 = no errors (warnings may still be printed), 1 = errors, 2 = unreadable

import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SEATS = new Set([
  "theory_builder",
  "causal_scientist",
  "measurement_scientist",
  "replication_scientist",
  "boundary_scientist",
  "adversarial_falsifier",
  "evidence_auditor",
]);

const LEVELS = new Set(["A", "B", "C", "D"]);
const DESIGNS = new Set([
  "experiment",
  "quasi_experiment",
  "longitudinal",
  "cross_sectional",
  "meta_analysis",
  "qualitative",
  "review",
]);
// Designs that can carry a causal claim; everything else is associational at
// best, whatever the paper concludes.
const CAUSAL_DESIGNS = new Set([
  "experiment",
  "quasi_experiment",
  "longitudinal",
  "meta_analysis",
]);
const CLAIM_TYPES = new Set([
  "causal",
  "associational",
  "measurement",
  "boundary",
  "mechanism",
]);
const STATUSES = new Set(["supported", "contested", "insufficient"]);
const REFUTING = new Set(["REFUTES", "CONFOUNDS"]);
const EDGE_TYPES = new Set([
  "SUPPORTS",
  "REFUTES",
  "QUALIFIES",
  "CONTRADICTS",
  "CONFOUNDS",
  "MEDIATES",
  "MODERATES",
  "OPERATIONALIZES",
  "DERIVED_FROM",
  "APPLIES_IN",
  "EXPOSES",
  "TESTS",
]);

const errors = [];
const warnings = [];
const err = (m) => errors.push(m);
const warn = (m) => warnings.push(m);
const isText = (v) => typeof v === "string" && v.trim().length > 0;
const isRecord = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isList = (v) => Array.isArray(v);

function resolveTarget(arg) {
  if (!arg) return null;
  try {
    if (statSync(arg).isDirectory()) return join(arg, "evidence.json");
  } catch {
    return null;
  }
  return arg;
}

function load(target) {
  try {
    return JSON.parse(readFileSync(target, "utf8"));
  } catch (cause) {
    console.error(`check_evidence: cannot read ${target}: ${cause.message}`);
    process.exit(2);
  }
}

/** Index a list by its id field; report duplicates and missing ids. */
function index(list, field, label) {
  const map = new Map();
  if (!isList(list)) {
    err(`${label} must be an array`);
    return map;
  }
  for (const [i, item] of list.entries()) {
    if (!isRecord(item)) {
      err(`${label}[${i}] is not an object`);
      continue;
    }
    const id = item[field];
    if (!isText(id)) {
      err(`${label}[${i}] has no ${field}`);
      continue;
    }
    if (map.has(id)) {
      err(`${label} has a duplicate ${field}: ${id}`);
      continue;
    }
    map.set(id, item);
  }
  return map;
}

function checkAnchors(findings, sources) {
  for (const [id, f] of findings) {
    if (!sources.has(f.source_id)) {
      err(`finding ${id} cites unknown source_id ${JSON.stringify(f.source_id)}`);
    }
    if (!isText(f.locator)) err(`finding ${id} has no locator (section/page)`);
    if (!isText(f.quote)) {
      err(`finding ${id} has no quote -- a finding without source text is not a finding`);
    } else if (f.quote.trim().length < 15) {
      err(`finding ${id} quote is too short to verify: ${JSON.stringify(f.quote)}`);
    }
    if (!DESIGNS.has(f.design)) {
      err(`finding ${id} has design ${JSON.stringify(f.design)}; expected one of ${[...DESIGNS].join(", ")}`);
    }
    if (!isText(f.dataset_key)) {
      warn(`finding ${id} has no dataset_key -- it cannot be counted into an evidence cluster`);
    }
  }
}

function checkEdges(edges, claims, findings) {
  for (const [i, e] of (isList(edges) ? edges : []).entries()) {
    if (!isRecord(e)) {
      err(`edges[${i}] is not an object`);
      continue;
    }
    if (!findings.has(e.from)) err(`edges[${i}].from ${JSON.stringify(e.from)} is not a finding`);
    if (!claims.has(e.to)) err(`edges[${i}].to ${JSON.stringify(e.to)} is not a claim`);
    if (!EDGE_TYPES.has(e.type)) {
      err(`edges[${i}].type ${JSON.stringify(e.type)} is not an evidence edge type`);
    }
  }
}

function checkCausalUpgrade(claims, findings, sources, edges, consensus) {
  for (const [claimId, claim] of claims) {
    if (!CLAIM_TYPES.has(claim.claim_type)) {
      err(`claim ${claimId} has claim_type ${JSON.stringify(claim.claim_type)}; expected one of ${[...CLAIM_TYPES].join(", ")}`);
    }
    if (!isText(claim.scope)) warn(`claim ${claimId} states no scope`);
    if (!isText(claim.statement)) err(`claim ${claimId} has no statement`);

    const incoming = (isList(edges) ? edges : []).filter((e) => isRecord(e) && e.to === claimId);
    if (incoming.length === 0) {
      err(`claim ${claimId} has no evidence edges -- a claim nobody anchored is not a finding`);
      continue;
    }

    const status = consensus.get(claimId)?.status;
    if (claim.claim_type !== "causal" || status !== "supported") continue;

    // Correlation does not upgrade to causation: a supported causal claim
    // needs at least one supporting finding whose design can identify an
    // effect, quoted from full text.
    const supports = incoming.filter((e) => e.type === "SUPPORTS");
    const strong = supports.filter((e) => {
      const f = findings.get(e.from);
      if (!f || !CAUSAL_DESIGNS.has(f.design)) return false;
      return sources.get(f.source_id)?.level === "A";
    });
    if (supports.length === 0) {
      err(`claim ${claimId} is a causal claim marked supported with no SUPPORTS edge`);
    } else if (strong.length === 0) {
      const designs = supports
        .map((e) => findings.get(e.from)?.design ?? "?")
        .join(", ");
      err(
        `claim ${claimId} is a supported causal claim whose only support is ` +
          `[${designs}] at abstract/second-hand level -- this is the correlation-to-causation upgrade`,
      );
    }
  }
}

function checkRefutation(claims, edges, consensus, dissent) {
  const byClaim = new Map();
  for (const e of (isList(edges) ? edges : [])) {
    if (!isRecord(e) || !REFUTING.has(e.type)) continue;
    if (!byClaim.has(e.to)) byClaim.set(e.to, []);
    byClaim.get(e.to).push(e);
  }
  for (const [claimId, refuting] of byClaim) {
    if (!claims.has(claimId)) continue;
    const status = consensus.get(claimId)?.status;
    for (const e of refuting) {
      if (status === "supported" && !isText(e.resolution)) {
        err(
          `claim ${claimId} is published as supported while ${e.from} ${e.type} it -- ` +
            `either mark it contested or give edge ${e.from}->${claimId} a resolution`,
        );
      }
    }
    if (status === "contested" && !dissent.some((d) => isList(d.against) && d.against.includes(claimId))) {
      err(`claim ${claimId} is contested but no dissent certificate names it`);
    }
  }
}

function checkConsensus(claims, consensusList) {
  const map = new Map();
  for (const [i, entry] of (isList(consensusList) ? consensusList : []).entries()) {
    if (!isRecord(entry)) {
      err(`consensus[${i}] is not an object`);
      continue;
    }
    const id = entry.claim_id;
    if (!claims.has(id)) {
      err(`consensus[${i}] refers to unknown claim ${JSON.stringify(id)}`);
      continue;
    }
    if (map.has(id)) {
      err(`consensus states claim ${id} twice`);
      continue;
    }
    if (!STATUSES.has(entry.status)) {
      err(`consensus[${id}].status ${JSON.stringify(entry.status)} is not one of ${[...STATUSES].join(", ")}`);
    }
    if (entry.status === "supported" && (!isList(entry.conditions) || entry.conditions.filter(isText).length === 0)) {
      err(`claim ${id} is supported with no condition under which it would fail`);
    }
    map.set(id, entry);
  }
  for (const id of claims.keys()) {
    if (!map.has(id)) err(`claim ${id} has no consensus status`);
  }
  return map;
}

function checkDissent(dissent) {
  for (const [id, d] of dissent) {
    if (!SEATS.has(d.seat)) err(`${id} names seat ${JSON.stringify(d.seat)}, which is not a council seat`);
    if (!isText(d.position)) err(`${id} states no position`);
    if (!isList(d.against) || d.against.length === 0) err(`${id} is against nothing`);
    if (!isText(d.resolving_evidence)) {
      err(`${id} says nothing about what evidence would resolve it`);
    }
  }
}

function checkBlindspots(blindspots, claims, gaps) {
  for (const [id, b] of blindspots) {
    if (!isText(b.statement)) err(`${id} states nothing`);
    for (const field of ["impact", "uncertainty", "investigability"]) {
      const value = b[field];
      if (!Number.isInteger(value) || value < 1 || value > 5) {
        err(`${id}.${field} must be an integer 1-5, got ${JSON.stringify(value)}`);
      }
    }
    for (const claimId of isList(b.exposed_by) ? b.exposed_by : []) {
      if (!claims.has(claimId)) err(`${id} is exposed_by unknown claim ${JSON.stringify(claimId)}`);
    }
  }
  if (blindspots.size === 0 && (!isList(gaps) || gaps.filter(isText).length === 0)) {
    err(
      "no blindspots were reported and no gaps explain why -- an empty blindspot list " +
        "on a contested question is a claim in itself and needs a reason",
    );
  }
}

function checkQuarantine(quarantined) {
  for (const [i, q] of (isList(quarantined) ? quarantined : []).entries()) {
    if (!isRecord(q)) {
      err(`quarantined[${i}] is not an object`);
      continue;
    }
    if (!isText(q.item)) err(`quarantined[${i}] names no item`);
    if (!isText(q.reason)) {
      err(`quarantined[${i}] (${q.item}) has no reason -- quarantine is not deletion, and an unexplained quarantine is indistinguishable from one`);
    }
    if (!isText(q.kept_statement)) {
      warn(`quarantined[${i}] (${q.item}) keeps no original statement`);
    }
  }
}

function checkCounts(counts, sources, findings) {
  if (!isRecord(counts)) {
    err("counts is missing");
    return;
  }
  const clusters = new Set(
    [...findings.values()].map((f) => f.dataset_key).filter(isText),
  );
  if (counts.papers !== sources.size) {
    err(`counts.papers is ${counts.papers} but the graph holds ${sources.size} sources`);
  }
  if (counts.independent_clusters !== clusters.size) {
    err(
      `counts.independent_clusters is ${counts.independent_clusters} but the findings ` +
        `name ${clusters.size} distinct datasets -- recount before reporting`,
    );
  }
  if (clusters.size === sources.size && sources.size > 1) {
    warn(
      `every source maps to its own dataset (${sources.size} clusters). Verify no ` +
        "shared dataset, overlapping sample, or preprint/published pair was missed.",
    );
  }
}

function checkLevels(sources) {
  for (const [id, s] of sources) {
    if (!LEVELS.has(s.level)) {
      err(`source ${id} has level ${JSON.stringify(s.level)}; expected A, B, C or D`);
    }
    if (!isText(s.title)) err(`source ${id} has no title`);
    if (s.level === "D") {
      warn(`source ${id} is a level D lead; it may guide acquisition but not support a claim`);
    }
  }
}

function main() {
  const target = resolveTarget(process.argv[2]);
  if (!target) {
    console.error("usage: node check_evidence.mjs <evidence.json | directory>");
    process.exit(2);
  }
  const data = load(target);
  if (!isRecord(data)) {
    console.error("check_evidence: evidence.json must hold one object");
    process.exit(2);
  }
  if (!isText(data.question)) err("question is missing");

  const sources = index(data.sources, "source_id", "sources");
  const findings = index(data.findings, "finding_id", "findings");
  const claims = index(data.claims, "claim_id", "claims");
  const blindspots = index(data.blindspots, "blindspot_id", "blindspots");
  const dissent = index(data.dissent, "dissent_id", "dissent");

  checkLevels(sources);
  checkAnchors(findings, sources);
  checkEdges(data.edges, claims, findings);
  const consensus = checkConsensus(claims, data.consensus);
  checkCausalUpgrade(claims, findings, sources, data.edges, consensus);
  checkRefutation(claims, data.edges, consensus, [...dissent.values()]);
  checkDissent(dissent);
  checkBlindspots(blindspots, claims, data.gaps);
  checkQuarantine(data.quarantined);
  checkCounts(data.counts, sources, findings);

  for (const w of warnings) console.log(`WARN  ${w}`);
  for (const e of errors) console.log(`FAIL  ${e}`);
  console.log(
    `\n${errors.length === 0 ? "OK" : "FAILED"}  ${sources.size} sources, ` +
      `${findings.size} findings, ${claims.size} claims, ` +
      `${blindspots.size} blindspots, ${dissent.size} dissent certificates, ` +
      `${errors.length} errors, ${warnings.length} warnings`,
  );
  process.exit(errors.length === 0 ? 0 : 1);
}

main();
