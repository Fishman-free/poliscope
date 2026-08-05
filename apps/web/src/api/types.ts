/** Mirrors the API contracts in apps/api/schemas.py and packages/reports.
 *
 * Written by hand rather than generated so the client states what it actually
 * relies on. Where the server returns an open payload the type says `unknown`
 * instead of `any`: a field the interface has not thought about must not be
 * renderable by accident, because in this product an unlabelled value on screen
 * reads as a verified finding.
 */

export type EvidenceLevel = "A" | "B" | "C" | "D";

/** Node status as written by the Graph Projector. Nothing is ever deleted, so
 * every outcome -- including refutation -- is one of these. */
export type NodeStatus =
  | "active"
  | "provisional"
  | "refuted"
  | "narrowed"
  | "withdrawn"
  | "quarantined";

export interface GraphNode {
  id: string;
  node_type: string;
  status: NodeStatus | string;
  payload: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  edge_type: string;
}

export interface EvidenceGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ConfirmedClaim {
  claim_id: string;
  statement: string;
  claim_type: string;
  falsification_condition: string;
}

export interface BriefNode {
  id: string;
  node_type: string;
  status: string;
  payload: Record<string, unknown>;
}

/** The Research Brief. `limitations` is not optional and is not a footnote --
 * CLAUDE.md 11 requires it beside the conclusions. */
export interface ResearchBrief {
  task_id: string;
  question: string;
  status: string;
  confirmed_claims: ConfirmedClaim[];
  findings: BriefNode[];
  blindspots: BriefNode[];
  dissents: BriefNode[];
  discriminating_studies: BriefNode[];
  refuted_or_withdrawn: BriefNode[];
  limitations: string[];
  unadmitted_events: string[];
  absent_seats: string[];
  failed_phases: string[];
  skipped_phases: string[];
  paper_count: number;
  independent_cluster_count: number;
  has_gaps: boolean;
  is_mental_health: boolean;
}

export interface SafetyNotice {
  classification: string;
  medical_disclaimer: string;
  limitations: string;
}

export interface TaskSummary {
  task_id: string;
  question: string;
  status: string;
  created_by: string;
}

/** One claim the council suggested from the raw question, straight off
 * `POST /api/tasks`'s `suggested_claims`. Nothing here is confirmed yet --
 * that only happens once the researcher picks which ids to keep and calls
 * `confirmClaims`. */
export interface SuggestedClaim {
  id: string;
  statement: string;
  claim_type: string;
  falsification_condition: string;
}

/** `POST /api/tasks`'s response shape. The task exists and is sitting at
 * `AWAITING_CLAIM_CONFIRMATION` -- nothing has been queued yet. */
export interface CreateTaskResult {
  task_id: string;
  status: string;
  suggested_claims: SuggestedClaim[];
}

/** `POST /api/tasks/{id}/confirm-claims`'s response shape. `status` is the
 * queued task's new status; discarded claims are not in `claims` removed --
 * CLAUDE.md 5.3 forbids that -- they come back with their own status. */
export interface ConfirmClaimsResult {
  task_id: string;
  status: string;
  claims: { id: string; status: string }[];
}

/** `POST /api/tasks/{id}/papers/upload`'s response shape. The server patches
 * the task's stored user_evidence; the client only shows the id back. */
export interface UploadedPaper {
  object_id: string;
  size_bytes: number;
}

/** Knowledge base list entry (`GET /api/knowledge-bases`). */
export interface KnowledgeBaseSummary {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  document_count: number;
}

/** One document inside a knowledge base. `text` is absent in listings; the
 * preview endpoint returns it, server-side truncated with `truncated` set. */
export interface KnowledgeDocumentSummary {
  document_id: string;
  title: string;
  size_bytes: number;
  page_count: number;
  created_at: string;
}

/** `GET /api/knowledge-bases/{id}`: the base plus its document listing. */
export interface KnowledgeBaseDetail extends KnowledgeBaseSummary {
  documents: KnowledgeDocumentSummary[];
}

/** `GET /api/knowledge-bases/{id}/documents/{docId}`: preview text included,
 * truncated at 20k characters with `truncated` flagging the cut. */
export interface KnowledgeDocumentDetail extends KnowledgeDocumentSummary {
  text: string;
  truncated: boolean;
}

/** One seat's precommitment, straight off ``PRECOMMITMENT_SEALED``. */
export interface SeatPrecommitment {
  confidence: number | null;
  update_condition: string | null;
}

/** One seat's challenge, straight off ``CHALLENGE_RAISED``. */
export interface SeatChallenge {
  claim_id: string | null;
  statement: string | null;
  is_fatal: boolean | null;
}

/** One seat's final judgment, straight off ``FINAL_JUDGMENT``. */
export interface SeatFinalJudgment {
  final_judgment: string | null;
  confidence: number | null;
  has_dissent: boolean | null;
}

/** Per-seat council summary. Absent phases stay visible as `null`/`[]`, not
 * omitted -- an unavailable seat must not silently vanish from the panel. */
export interface SeatSummary {
  seat: string;
  precommitment: SeatPrecommitment | null;
  challenges_raised: SeatChallenge[];
  final_judgment: SeatFinalJudgment | null;
  unavailable_phases: string[];
}

/** A claim-referencing timeline entry: a Claim (incl. a fork), a challenge,
 * or a dissent. `claim_id` is `null` when the production code has no claim
 * to name for this event -- shown as such, never coerced to an empty string. */
export interface EvolutionEntry {
  sequence: number;
  event_type: string;
  status: string;
  claim_id: string | null;
  payload: Record<string, unknown>;
}

/** The flattened node shape `_nodes_of_type()` returns: id/status plus the
 * node's own payload spread in -- structurally distinct from `BriefNode`,
 * whose payload stays nested. Do not reuse `BriefNode` for these fields. */
export interface WorkspaceNode {
  id: string;
  status: string;
  [key: string]: unknown;
}

/** A Blindspot's own `kind`, not to be confused with its `node_type` (always
 * "Blindspot"). "bounty" carries all five scored dimensions; the source-
 * diversity check does not score anything, and must be shown as unscored
 * rather than defaulted to 0 -- a 0 would misrepresent it as investigated
 * and found low-impact. */
export type BlindspotKind = "bounty" | "source_diversity" | string;

export interface WorkspaceBlindspot extends WorkspaceNode {
  statement?: string;
  kind?: BlindspotKind;
  score?: string;
  impact?: string;
  uncertainty?: string;
  investigability?: string;
  novelty?: string;
  normalized_cost?: string;
  source_refs?: string[];
}

export const BLINDSPOT_KIND_LABELS: Record<string, string> = {
  bounty: "议会提名",
  source_diversity: "来源单一",
  adversarial_retrieval: "对抗式检索",
};

export interface WorkspaceSnapshot {
  task: TaskSummary;
  brief: ResearchBrief;
  seats: SeatSummary[];
  graph: EvidenceGraph;
  blindspots: WorkspaceBlindspot[];
  discriminating_studies: WorkspaceNode[];
  dissents: WorkspaceNode[];
  evolution: EvolutionEntry[];
  paper_count: number;
  independent_cluster_count: number;
  workspace_version: number;
  safety_notice: SafetyNotice;
}

/** One row of the audit trail, straight off the Scientific Event Ledger. */
export interface LedgerEvent {
  event_id: string;
  task_id: string;
  kind: string;
  workspace_version: number;
  payload: Record<string, unknown>;
}

export const SEATS = [
  "theory_builder",
  "causal_scientist",
  "measurement_scientist",
  "replication_scientist",
  "boundary_scientist",
  "adversarial_falsifier",
  "evidence_auditor",
] as const;

export type Seat = (typeof SEATS)[number];

export const SEAT_LABELS: Record<Seat, string> = {
  theory_builder: "理论建构者",
  causal_scientist: "因果推断专家",
  measurement_scientist: "测量与构念专家",
  replication_scientist: "统计与复现专家",
  boundary_scientist: "边界与情境专家",
  adversarial_falsifier: "对抗性证伪者",
  evidence_auditor: "证据与溯源审计员",
};

/** Node types that carry a panel of their own. Everything else is graph-only. */
export const NODE_TYPE_LABELS: Record<string, string> = {
  ResearchQuestion: "研究问题",
  Claim: "主张",
  Source: "来源",
  StudyFinding: "研究发现",
  Construct: "构念",
  Context: "情境",
  Blindspot: "盲点",
  DebateCapsule: "争论胶囊",
  DiscriminatingStudy: "可区分性研究",
  DissentCertificate: "异议证书",
};

export const EDGE_TYPE_LABELS: Record<string, string> = {
  SUPPORTS: "支持",
  REFUTES: "反驳",
  QUALIFIES: "限定",
  CONTRADICTS: "冲突",
  CONFOUNDS: "混杂",
  MEDIATES: "中介",
  MODERATES: "调节",
  OPERATIONALIZES: "操作化",
  DERIVED_FROM: "来源于",
  APPLIES_IN: "适用于",
  EXPOSES: "暴露",
  TESTS: "检验",
};
