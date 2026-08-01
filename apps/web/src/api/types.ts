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

export interface WorkspaceSnapshot {
  task: TaskSummary;
  brief: ResearchBrief;
  seats: Record<string, unknown>[];
  graph: EvidenceGraph;
  blindspots: Record<string, unknown>[];
  discriminating_studies: Record<string, unknown>[];
  dissents: Record<string, unknown>[];
  evolution: Record<string, unknown>[];
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
