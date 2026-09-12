"""Versioned additive SQLite schemas for Phase 2A."""

from __future__ import annotations

import hashlib

from .config import SCHEMA_VERSION


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS phase2a_schema_migrations (
    schema_version TEXT PRIMARY KEY,
    schema_sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase2a_runs (
    run_id TEXT PRIMARY KEY,
    run_kind TEXT NOT NULL DEFAULT 'SHADOW',
    state_lineage TEXT NOT NULL,
    processing_status TEXT NOT NULL CHECK (processing_status IN ('STARTED','COMPLETE','PARTIAL','FAILED')),
    coverage_status TEXT NOT NULL CHECK (coverage_status IN ('FULL','DEGRADED','UNKNOWN')),
    publish_eligibility TEXT NOT NULL CHECK (publish_eligibility IN ('ELIGIBLE','ELIGIBLE_DEGRADED','BLOCKED')),
    state_commit_status TEXT NOT NULL CHECK (state_commit_status IN ('STAGING','COMMITTED','ROLLED_BACK')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    expected_market_date TEXT NOT NULL,
    market_session_index INTEGER NOT NULL CHECK (market_session_index >= 0),
    scope_name TEXT NOT NULL,
    scope_member_sha256 TEXT NOT NULL,
    scope_total INTEGER NOT NULL CHECK (scope_total >= 0),
    current_total INTEGER NOT NULL DEFAULT 0 CHECK (current_total >= 0),
    no_new_market_total INTEGER NOT NULL DEFAULT 0 CHECK (no_new_market_total >= 0),
    stale_total INTEGER NOT NULL DEFAULT 0 CHECK (stale_total >= 0),
    insufficient_total INTEGER NOT NULL DEFAULT 0 CHECK (insufficient_total >= 0),
    fetch_failed_total INTEGER NOT NULL DEFAULT 0 CHECK (fetch_failed_total >= 0),
    analysis_failed_total INTEGER NOT NULL DEFAULT 0 CHECK (analysis_failed_total >= 0),
    out_of_scope_total INTEGER NOT NULL DEFAULT 0 CHECK (out_of_scope_total >= 0),
    invalid_input_total INTEGER NOT NULL DEFAULT 0 CHECK (invalid_input_total >= 0),
    identity_link_total INTEGER NOT NULL DEFAULT 0 CHECK (identity_link_total >= 0),
    identity_mint_total INTEGER NOT NULL DEFAULT 0 CHECK (identity_mint_total >= 0),
    identity_ambiguous_total INTEGER NOT NULL DEFAULT 0 CHECK (identity_ambiguous_total >= 0),
    no_setup_total INTEGER NOT NULL DEFAULT 0 CHECK (no_setup_total >= 0),
    transition_total INTEGER NOT NULL DEFAULT 0 CHECK (transition_total >= 0),
    unchanged_total INTEGER NOT NULL DEFAULT 0 CHECK (unchanged_total >= 0),
    rejected_total INTEGER NOT NULL DEFAULT 0 CHECK (rejected_total >= 0),
    input_sha256 TEXT NOT NULL,
    seed_sha256 TEXT,
    seed_schema_version TEXT,
    seed_row_count INTEGER,
    versions_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    structured_errors_json TEXT NOT NULL,
    UNIQUE (state_lineage, expected_market_date),
    UNIQUE (state_lineage, input_sha256)
);

CREATE TABLE IF NOT EXISTS phase2a_scope_members (
    run_id TEXT NOT NULL REFERENCES phase2a_runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL CHECK (length(code) = 4),
    in_scope INTEGER NOT NULL CHECK (in_scope IN (0,1)),
    observation_status TEXT NOT NULL,
    analysis_date TEXT,
    latest_price_date TEXT,
    price_history_count INTEGER,
    required_price_history_count INTEGER,
    reason_codes_json TEXT NOT NULL,
    structured_error_ref TEXT,
    observation_uid TEXT,
    PRIMARY KEY (run_id, code)
);

CREATE TABLE IF NOT EXISTS phase2a_identity_ledger (
    core_setup_uid TEXT PRIMARY KEY,
    code TEXT NOT NULL CHECK (length(code) = 4),
    namespace TEXT NOT NULL,
    mint_request_sha256 TEXT NOT NULL UNIQUE,
    canonical_mint_request TEXT NOT NULL,
    identity_epoch TEXT NOT NULL,
    origin_observation_uid TEXT NOT NULL,
    origin_slot TEXT NOT NULL,
    identity_version TEXT NOT NULL,
    created_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    terminal INTEGER NOT NULL DEFAULT 0 CHECK (terminal IN (0,1))
);

CREATE TABLE IF NOT EXISTS phase2a_observations (
    observation_uid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES phase2a_runs(run_id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    code TEXT NOT NULL CHECK (length(code) = 4),
    analysis_date TEXT NOT NULL,
    expected_market_date TEXT NOT NULL,
    market_session_index INTEGER NOT NULL CHECK (market_session_index >= 0),
    observed_at TEXT,
    close REAL NOT NULL CHECK (close > 0),
    previous_accepted_close REAL,
    core_observed_state TEXT NOT NULL,
    trend_strategy_states_json TEXT NOT NULL,
    connors_state TEXT,
    aligned_trend_strategy_count INTEGER NOT NULL CHECK (aligned_trend_strategy_count >= 0),
    breakout_trend_strategy_count INTEGER NOT NULL CHECK (breakout_trend_strategy_count >= 0),
    observed_pivot_price REAL NOT NULL CHECK (observed_pivot_price > 0),
    observed_pivot_strategy TEXT NOT NULL,
    observed_pivot_type TEXT NOT NULL,
    observed_pivot_basis TEXT NOT NULL,
    observed_pivot_fidelity TEXT NOT NULL,
    observed_pivot_reference_date TEXT,
    observation_status TEXT NOT NULL CHECK (observation_status = 'CURRENT'),
    decision_slot TEXT NOT NULL,
    core_setup_uid TEXT REFERENCES phase2a_identity_ledger(core_setup_uid),
    tracking_pivot_revision_no INTEGER,
    source_versions_json TEXT NOT NULL,
    legacy_refs_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    UNIQUE (namespace, code, analysis_date, input_sha256)
);

CREATE TABLE IF NOT EXISTS phase2a_strategy_identity_ledger (
    strategy_setup_uid TEXT PRIMARY KEY,
    code TEXT NOT NULL CHECK (length(code) = 4),
    strategy_code TEXT NOT NULL,
    mint_request_sha256 TEXT NOT NULL UNIQUE,
    canonical_mint_request TEXT NOT NULL,
    identity_epoch TEXT NOT NULL,
    origin_observation_uid TEXT NOT NULL,
    identity_version TEXT NOT NULL,
    created_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase2a_identity_decisions (
    decision_uid TEXT PRIMARY KEY,
    observation_uid TEXT NOT NULL REFERENCES phase2a_observations(observation_uid) ON DELETE CASCADE,
    code TEXT NOT NULL CHECK (length(code) = 4),
    namespace TEXT NOT NULL,
    decision_slot TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('LINK','MINT','AMBIGUOUS','NO_SETUP')),
    target_core_setup_uid TEXT REFERENCES phase2a_identity_ledger(core_setup_uid),
    identity_epoch TEXT,
    origin_slot TEXT,
    decision_rule_version TEXT NOT NULL,
    blocker TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    supersedes_decision_uid TEXT REFERENCES phase2a_identity_decisions(decision_uid),
    UNIQUE (observation_uid, decision_slot)
);

CREATE TABLE IF NOT EXISTS phase2a_pivot_revisions (
    core_setup_uid TEXT NOT NULL REFERENCES phase2a_identity_ledger(core_setup_uid),
    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
    tracking_pivot_price REAL NOT NULL CHECK (tracking_pivot_price > 0),
    strategy TEXT NOT NULL,
    pivot_type TEXT NOT NULL,
    basis TEXT NOT NULL,
    fidelity TEXT NOT NULL,
    reference_date TEXT,
    effective_observation_uid TEXT NOT NULL,
    valid_from_market_session_index INTEGER NOT NULL CHECK (valid_from_market_session_index >= 0),
    valid_to_market_session_index INTEGER,
    frozen_after_breakout INTEGER NOT NULL CHECK (frozen_after_breakout IN (0,1)),
    evidence_sha256 TEXT NOT NULL,
    PRIMARY KEY (core_setup_uid, revision_no)
);

CREATE TABLE IF NOT EXISTS phase2a_strategy_memberships (
    core_setup_uid TEXT NOT NULL REFERENCES phase2a_identity_ledger(core_setup_uid),
    strategy_code TEXT NOT NULL,
    strategy_setup_uid TEXT NOT NULL REFERENCES phase2a_strategy_identity_ledger(strategy_setup_uid),
    member_from_observation_uid TEXT NOT NULL,
    member_to_observation_uid TEXT,
    valid_from_market_session_index INTEGER NOT NULL,
    valid_to_market_session_index INTEGER,
    evidence_sha256 TEXT NOT NULL,
    PRIMARY KEY (core_setup_uid, strategy_code, strategy_setup_uid, member_from_observation_uid)
);

CREATE TABLE IF NOT EXISTS phase2a_events (
    event_uid TEXT PRIMARY KEY,
    event_request_sha256 TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    occurrence_ordinal INTEGER NOT NULL CHECK (occurrence_ordinal > 0),
    core_setup_uid TEXT NOT NULL REFERENCES phase2a_identity_ledger(core_setup_uid),
    from_phase TEXT,
    to_phase TEXT NOT NULL,
    is_phase_transition INTEGER NOT NULL CHECK (is_phase_transition IN (0,1)),
    effective_date TEXT NOT NULL,
    effective_date_status TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    observation_uid TEXT,
    permanent_exit_evidence_ref TEXT,
    tracking_pivot_revision_no INTEGER,
    prior_related_event_uid TEXT,
    state_machine_version TEXT NOT NULL,
    threshold_version TEXT NOT NULL,
    transition_id TEXT,
    reason_codes_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    producer_run_id TEXT NOT NULL REFERENCES phase2a_runs(run_id),
    corrects_event_uid TEXT,
    CHECK (event_type = 'SETUP_CLOSED' OR observation_uid IS NOT NULL),
    CHECK (event_type != 'SETUP_CLOSED' OR permanent_exit_evidence_ref IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS phase2a_current_states (
    state_lineage TEXT NOT NULL,
    core_setup_uid TEXT NOT NULL REFERENCES phase2a_identity_ledger(core_setup_uid),
    code TEXT NOT NULL CHECK (length(code) = 4),
    current_phase TEXT NOT NULL CHECK (current_phase IN ('FORMING','WATCH','POST_BREAKOUT','FAILED','RETRY_WATCH','EXPIRED','CLOSED')),
    continuity_status TEXT NOT NULL CHECK (continuity_status IN ('CONTIGUOUS','PAUSED','SUSPENDED_GAP','TERMINAL')),
    distribution_eligible INTEGER NOT NULL CHECK (distribution_eligible IN (0,1)),
    phase_entered_observation_uid TEXT,
    phase_entered_effective_date TEXT,
    latest_accepted_observation_uid TEXT,
    latest_accepted_market_session_index INTEGER NOT NULL,
    latest_accepted_date TEXT NOT NULL,
    latest_input_sha256 TEXT NOT NULL,
    latest_accepted_close REAL NOT NULL CHECK (latest_accepted_close > 0),
    tracking_pivot_revision_no INTEGER NOT NULL CHECK (tracking_pivot_revision_no > 0),
    latest_event_uid TEXT,
    latest_breakout_event_uid TEXT,
    latest_failure_event_uid TEXT,
    minted_market_session_index INTEGER NOT NULL,
    latest_breakout_market_session_index INTEGER,
    breakout_count INTEGER NOT NULL DEFAULT 0 CHECK (breakout_count >= 0),
    failure_cycle_no INTEGER NOT NULL DEFAULT 0 CHECK (failure_cycle_no >= 0),
    closure_reason_code TEXT,
    closure_evidence_ref TEXT,
    state_machine_version TEXT NOT NULL,
    threshold_version TEXT NOT NULL,
    state_version INTEGER NOT NULL CHECK (state_version > 0),
    updated_run_id TEXT NOT NULL,
    PRIMARY KEY (state_lineage, core_setup_uid)
);

CREATE TABLE IF NOT EXISTS phase2a_rejections (
    rejection_uid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES phase2a_runs(run_id) ON DELETE CASCADE,
    observation_uid TEXT,
    candidate_core_setup_uid TEXT,
    action TEXT NOT NULL,
    transition_id TEXT,
    reason_codes_json TEXT NOT NULL,
    route TEXT,
    state_machine_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase2a_replay_lineages (
    replay_lineage TEXT PRIMARY KEY,
    source_lineage TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    approved_manifest_sha256 TEXT,
    merged_to_live INTEGER NOT NULL DEFAULT 0 CHECK (merged_to_live = 0)
);

CREATE TABLE IF NOT EXISTS phase2a_event_anchors (
    core_setup_uid TEXT NOT NULL REFERENCES phase2a_identity_ledger(core_setup_uid),
    anchor_type TEXT NOT NULL,
    event_uid TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    market_session_index INTEGER,
    occurrence_ordinal INTEGER NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    PRIMARY KEY (core_setup_uid, anchor_type)
);

CREATE TABLE IF NOT EXISTS phase2a_seed_manifests (
    seed_sha256 TEXT PRIMARY KEY,
    seed_schema_version TEXT NOT NULL,
    producer_run_id TEXT,
    state_lineage TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    row_counts_json TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS phase2a_failed_run_manifests (
    failure_id TEXT PRIMARY KEY,
    attempted_run_id TEXT NOT NULL,
    state_lineage TEXT NOT NULL,
    expected_market_date TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    failed_at TEXT NOT NULL,
    stage TEXT,
    reason_codes_json TEXT NOT NULL,
    error_digest TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS phase2a_idx_scope_status ON phase2a_scope_members(run_id, observation_status);
CREATE INDEX IF NOT EXISTS phase2a_idx_observation_run ON phase2a_observations(run_id);
CREATE INDEX IF NOT EXISTS phase2a_idx_observation_setup_date ON phase2a_observations(core_setup_uid, analysis_date);
CREATE INDEX IF NOT EXISTS phase2a_idx_identity_code ON phase2a_identity_ledger(code, terminal);
CREATE INDEX IF NOT EXISTS phase2a_idx_strategy_identity_code ON phase2a_strategy_identity_ledger(code, strategy_code);
CREATE INDEX IF NOT EXISTS phase2a_idx_decision_target ON phase2a_identity_decisions(target_core_setup_uid);
CREATE INDEX IF NOT EXISTS phase2a_idx_pivot_effective ON phase2a_pivot_revisions(core_setup_uid, valid_from_market_session_index);
CREATE INDEX IF NOT EXISTS phase2a_idx_membership_active ON phase2a_strategy_memberships(core_setup_uid, valid_to_market_session_index);
CREATE INDEX IF NOT EXISTS phase2a_idx_event_setup_date ON phase2a_events(core_setup_uid, effective_date);
CREATE INDEX IF NOT EXISTS phase2a_idx_event_run ON phase2a_events(producer_run_id);
CREATE INDEX IF NOT EXISTS phase2a_idx_state_code ON phase2a_current_states(code, current_phase);
"""


SCHEMA_SHA256 = hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()


SEED_SCHEMA_VERSION = "phase2a-seed-v1"

SEED_SCHEMA_SQL = r"""
CREATE TABLE seed_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE seed_identity_ledger (
    core_setup_uid TEXT PRIMARY KEY, code TEXT NOT NULL, namespace TEXT NOT NULL,
    mint_request_sha256 TEXT NOT NULL UNIQUE, canonical_mint_request TEXT NOT NULL,
    identity_epoch TEXT NOT NULL, origin_observation_uid TEXT NOT NULL,
    origin_slot TEXT NOT NULL, identity_version TEXT NOT NULL,
    created_run_id TEXT NOT NULL, created_at TEXT NOT NULL, terminal INTEGER NOT NULL
);
CREATE TABLE seed_strategy_identity_ledger (
    strategy_setup_uid TEXT PRIMARY KEY, code TEXT NOT NULL, strategy_code TEXT NOT NULL,
    mint_request_sha256 TEXT NOT NULL UNIQUE, canonical_mint_request TEXT NOT NULL,
    identity_epoch TEXT NOT NULL, origin_observation_uid TEXT NOT NULL,
    identity_version TEXT NOT NULL, created_run_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE seed_current_states (
    state_lineage TEXT NOT NULL, core_setup_uid TEXT NOT NULL, state_json TEXT NOT NULL,
    PRIMARY KEY (state_lineage, core_setup_uid)
);
CREATE TABLE seed_pivot_revisions (
    core_setup_uid TEXT NOT NULL, revision_no INTEGER NOT NULL, pivot_json TEXT NOT NULL,
    PRIMARY KEY (core_setup_uid, revision_no)
);
CREATE TABLE seed_strategy_memberships (
    core_setup_uid TEXT NOT NULL, strategy_code TEXT NOT NULL,
    strategy_setup_uid TEXT NOT NULL, member_from_observation_uid TEXT NOT NULL,
    membership_json TEXT NOT NULL,
    PRIMARY KEY (core_setup_uid, strategy_code, strategy_setup_uid, member_from_observation_uid)
);
CREATE TABLE seed_event_anchors (
    core_setup_uid TEXT NOT NULL, anchor_type TEXT NOT NULL, anchor_json TEXT NOT NULL,
    PRIMARY KEY (core_setup_uid, anchor_type)
);
"""


def schema_identity() -> tuple[str, str]:
    return SCHEMA_VERSION, SCHEMA_SHA256
