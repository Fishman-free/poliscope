"""JSON Schemas for the seven structured outputs the council's rounds expect.

The field names here are not invented -- they are read directly out of
``packages/council/rounds/registry.py``, the only code that actually consumes
a seat's answer, and the schema *names* match
``packages.council.deliberation.PHASE_OUTPUT_SCHEMAS`` exactly. If a round's
expected field changes, this file and the registry must change together, or a
real vendor call will parse cleanly while feeding the wrong shape downstream.
"""

from __future__ import annotations

from typing import Any, Final

PRECOMMITMENT_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "initial_judgment": {"type": "string"},
        "confidence": {"type": "number", "description": "0 to 1"},
        "blindspots": {"type": "array", "items": {"type": "string"}},
        "update_condition": {"type": "string"},
    },
    "required": ["initial_judgment", "confidence", "blindspots", "update_condition"],
}

ACQUISITION_REQUESTS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "requests": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["requests"],
}

EVIDENCE_PROJECTION: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "evidence_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": (
                            "UUID of a Source already returned by acquisition"
                        ),
                    },
                    "anchor_summary": {"type": "string"},
                    "level": {"type": "string", "description": "A, B, C, or D"},
                },
                "required": ["source_id", "anchor_summary", "level"],
            },
        },
    },
    "required": ["evidence_items"],
}

CHALLENGE_SET: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "challenges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {
                        "type": "string",
                        "description": "UUID of a confirmed atomic claim",
                    },
                    "statement": {"type": "string"},
                    "is_fatal": {"type": "boolean"},
                    "fork": {
                        "type": "object",
                        "description": (
                            "Only consulted when is_fatal is true. Produces a "
                            "parallel Claim node (packages.council.rounds."
                            "registry._fork_events) instead of silently "
                            "dropping a disagreement that QUALIFY cannot "
                            "reconcile."
                        ),
                        "properties": {
                            "statement": {"type": "string"},
                            "scope": {"type": "object"},
                            "falsification_condition": {"type": "string"},
                            "claim_type": {
                                "type": "string",
                                "description": (
                                    "causal, correlational, measurement, "
                                    "boundary, mechanism, or null_result -- "
                                    "self-reported because no independent "
                                    "model computes this in the MVP. An "
                                    "unrecognised value falls back to "
                                    "correlational rather than being guessed "
                                    "as causal."
                                ),
                            },
                            "study_design": {
                                "type": "string",
                                "description": (
                                    "cross_sectional, longitudinal, "
                                    "experimental, quasi_experimental, "
                                    "qualitative, meta_analysis, or other -- "
                                    "same vocabulary as STUDY_FINDING_"
                                    "EXTRACTION's design field. Only "
                                    "meaningful alongside claim_type=causal; "
                                    "packages.evidence.causal_policy."
                                    "CausalUpgradePolicy checks the pair."
                                ),
                            },
                        },
                    },
                },
                "required": ["claim_id", "statement", "is_fatal"],
            },
        },
    },
    "required": ["challenges"],
}

BLINDSPOT_NOMINATIONS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "blindspots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "a UUID4 you generate for this blindspot",
                    },
                    "statement": {"type": "string"},
                    "impact": {"type": "number", "description": "0 to 1"},
                    "uncertainty": {"type": "number", "description": "0 to 1"},
                    "investigability": {"type": "number", "description": "0 to 1"},
                    "novelty": {"type": "number", "description": "0 to 1"},
                    "normalized_cost": {"type": "number", "description": "0 to 1"},
                },
                "required": [
                    "id",
                    "statement",
                    "impact",
                    "uncertainty",
                    "investigability",
                    "novelty",
                    "normalized_cost",
                ],
            },
        },
    },
    "required": ["blindspots"],
}

JOINT_MODEL_CONTRIBUTION: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "boundary_conditions": {"type": "array", "items": {"type": "string"}},
        "falsification_conditions": {"type": "array", "items": {"type": "string"}},
        "unresolved_conflicts": {"type": "array", "items": {"type": "string"}},
        "strongest_opposition_refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "UUIDs of the confirmed claims this seat opposes most",
        },
    },
    "required": [
        "boundary_conditions",
        "falsification_conditions",
        "unresolved_conflicts",
        "strongest_opposition_refs",
    ],
}

FINAL_JUDGMENT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "final_judgment": {"type": "string"},
    },
    "required": ["final_judgment"],
}

# System-level schema for packages.papers.finding_extraction.FindingExtractor --
# not one of the seven seat phase schemas above, so it is not subject to the
# seat/phase consistency contract that governs PHASE_OUTPUT_SCHEMAS. It shapes
# a single system model call that turns parsed PDF pages into a
# StudyFindingCandidate (packages/papers/contracts.py) via
# packages/papers/packet.py::build_packet(). The six method_quality sub-fields
# are exactly packages.evidence.gate._METHOD_SCORE_KEYS -- naming them anything
# else would leave Stage 5 of the evidence gate auditing nothing.
STUDY_FINDING_EXTRACTION: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "study_question": {"type": "string"},
        "population": {"type": "string"},
        "design": {
            "type": "string",
            "description": (
                "cross_sectional, longitudinal, experimental, "
                "quasi_experimental, qualitative, meta_analysis, or other"
            ),
        },
        "exposure_variable": {"type": "string"},
        "outcome_variable": {"type": "string"},
        "analysis_method": {"type": "string"},
        "finding_statement": {"type": "string"},
        "origin": {
            "type": "string",
            "description": "SOURCE_TEXT if stated by the authors, else AI_DERIVED",
        },
        "effect_direction": {
            "type": "string",
            "description": "positive, negative, null, or mixed",
        },
        "exact_quote": {
            "type": "string",
            "description": (
                "Verbatim text copied from the paper that supports the "
                "finding -- must be locatable in the source pages as-is."
            ),
        },
        "author_conclusions": {"type": "array", "items": {"type": "string"}},
        "author_limitations": {"type": "array", "items": {"type": "string"}},
        "data_availability": {
            "type": "string",
            "description": "public, restricted, unavailable, or not_reported",
        },
        "code_availability": {
            "type": "string",
            "description": "public, restricted, unavailable, or not_reported",
        },
        "preregistration": {
            "type": "string",
            "description": "public, restricted, unavailable, or not_reported",
        },
        "method_quality": {
            "type": "object",
            "properties": {
                "directness": {"type": "number", "description": "0 to 1"},
                "design_quality": {"type": "number", "description": "0 to 1"},
                "measurement_quality": {"type": "number", "description": "0 to 1"},
                "precision": {"type": "number", "description": "0 to 1"},
                "replicability": {"type": "number", "description": "0 to 1"},
                "external_validity": {"type": "number", "description": "0 to 1"},
            },
            "required": [
                "directness",
                "design_quality",
                "measurement_quality",
                "precision",
                "replicability",
                "external_validity",
            ],
        },
    },
    "required": [
        "study_question",
        "population",
        "design",
        "exposure_variable",
        "outcome_variable",
        "analysis_method",
        "finding_statement",
        "origin",
        "effect_direction",
        "exact_quote",
        "method_quality",
    ],
}

# The report synthesizer's output (packages/reports/synthesis.py). It is not
# a seat's answer and no round consumes it -- the worker stores the parsed
# result as FINAL_PAPER_DRAFTED, so the schema's only job is to keep the
# model honest about the paper's shape. `references[*].id` must name a
# source/finding id from the materials the synthesizer was given.
FINAL_PAPER_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "abstract": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "paragraphs"],
            },
        },
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "doi": {"type": ["string", "null"]},
                },
                "required": ["id", "title"],
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
        "investigation_process": {"type": "array", "items": {"type": "string"}},
        # Round-12 「整合结论详细化」: each side's position and weakness, the
        # overall conclusion, and the admitted evidence it rests on. Optional
        # so a vendor that omits them still produces a valid paper (the
        # parser defaults the fields to empty).
        "standpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string"},
                    "position": {"type": "string"},
                    "weakness": {"type": "string"},
                    "supporting_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "disagreement": {"type": "string"},
                },
                "required": ["seat", "position", "weakness"],
            },
        },
        "overall_conclusion": {"type": "string"},
        "conclusion_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "abstract",
        "sections",
        "references",
        "limitations",
        "investigation_process",
    ],
}

# The paper-review task's one-shot understanding call (round-7,
# packages/papers/understanding.py): the model reads the uploaded paper and
# states its research question, main claims with their supporting evidence,
# and anything it could not verify. Not a seat's answer -- the worker stores
# it as a process-only ledger event and injects it into every seat's prompt
# as explicitly non-evidence context.
PAPER_UNDERSTANDING_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "research_question": {"type": "string"},
        "main_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "description": "One claim the paper makes",
                    },
                    "supporting_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "What the paper offers in support (results, "
                            "citations, arguments), as stated in the paper"
                        ),
                    },
                    "locations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Page/paragraph locators in the uploaded text, "
                            "e.g. 'p.5' or 'para 12'"
                        ),
                    },
                },
                "required": ["statement", "supporting_evidence"],
            },
        },
        "unverifiable": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Claims or parts of the paper that could not be understood "
                "from the provided text -- must be admitted, not guessed"
            ),
        },
    },
    "required": ["title", "research_question", "main_claims"],
}

# The paper-review task's final report shape (round-7,
# packages/reports/synthesis.py). Same machinery as FINAL_PAPER_OUTPUT, but
# the subject is the *uploaded paper*: the report must state what the paper
# argues, then where its argument is not rigorous or well-evidenced, then how
# to improve it.
PAPER_REVIEW_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "paper_overview": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "research_question": {"type": "string"},
                "main_claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "supporting_evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["statement"],
                    },
                },
            },
            "required": ["research_question", "main_claims"],
        },
        "rigor_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_ref": {
                        "type": "string",
                        "description": (
                            "Which paper claim or part this issue concerns"
                        ),
                    },
                    "issue": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "description": "high, medium, or low",
                    },
                },
                "required": ["issue"],
            },
        },
        "evidence_insufficiency": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_ref": {"type": "string"},
                    "missing_evidence": {"type": "string"},
                    "suggested_evidence": {"type": "string"},
                },
                "required": ["missing_evidence"],
            },
        },
        "improvement_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_ref": {"type": "string"},
                    "issue": {"type": "string"},
                },
                "required": ["issue"],
            },
        },
        "conclusion": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "investigation_process": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "paper_overview",
        "rigor_issues",
        "evidence_insufficiency",
        "improvement_suggestions",
        "conclusion",
        "limitations",
        "investigation_process",
    ],
}

# Keyed by the exact schema name strings in
# packages.council.deliberation.PHASE_OUTPUT_SCHEMAS.
PHASE_OUTPUT_JSON_SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    "PrecommitmentOutput": PRECOMMITMENT_OUTPUT,
    "AcquisitionRequests": ACQUISITION_REQUESTS,
    "EvidenceProjection": EVIDENCE_PROJECTION,
    "ChallengeSet": CHALLENGE_SET,
    "BlindspotNominations": BLINDSPOT_NOMINATIONS,
    "JointModelContribution": JOINT_MODEL_CONTRIBUTION,
    "FinalJudgment": FINAL_JUDGMENT,
    "StudyFindingExtraction": STUDY_FINDING_EXTRACTION,
    # Not a council round: the report synthesizer's one-shot paper output
    # (packages/reports/synthesis.py). Same repair/quarantine machinery as the
    # rounds; a paper that cannot be repaired becomes FINAL_PAPER_FAILED.
    "FinalPaper": FINAL_PAPER_OUTPUT,
    # Round-7 system-level schemas: the paper-review task's one-shot
    # understanding call (packages/papers/understanding.py) and final report
    # (packages/reports/synthesis.py). Neither is a seat's answer.
    "PaperUnderstanding": PAPER_UNDERSTANDING_OUTPUT,
    "PaperReviewReport": PAPER_REVIEW_OUTPUT,
}

__all__ = ["PHASE_OUTPUT_JSON_SCHEMAS"]
