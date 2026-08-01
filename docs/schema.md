# Poliscope Schema

## Core Contracts

- `ContractModel`: Frozen Pydantic base with `extra="forbid"`, `frozen=True`
- `FrozenDict[K, V]`: Immutable Mapping with hash support
- `StrEnum` for all enumerations

## Evidence Graph

### Node Types (9)

- `ResearchQuestion`
- `Claim`
- `Source`
- `StudyFinding`
- `Construct`
- `Context`
- `Blindspot`
- `DebateCapsule`
- `DiscriminatingStudy`

### Edge Types (12)

- `SUPPORTS`, `REFUTES`, `QUALIFIES`, `CONTRADICTS`
- `CONFOUNDS`, `MEDIATES`, `MODERATES`
- `OPERATIONALIZES`, `DERIVED_FROM`, `APPLIES_IN`
- `EXPOSES`, `TESTS`

## A–D Evidence Level Matrix

| Level | Disposition |
|-------|-------------|
| A | ADMIT |
| B | SOURCE_ONLY |
| C | DISCOVERY_ONLY |
| D | TOOL_LEAD_ONLY |

## 7 Seats

1. `theory_builder`
2. `causal_scientist`
3. `measurement_scientist`
4. `replication_scientist`
5. `boundary_scientist`
6. `adversarial_falsifier`
7. `evidence_auditor`
