export type Role = "OWNER" | "RESEARCHER";

export interface Session {
  user_id: string;
  team_id: string;
  session_id: string;
  name: string;
  email: string;
  role: Role;
  csrf_token: string;
  recent_authentication: boolean;
  absolute_expires_at: string;
  agent_enabled: boolean;
}

export interface Project {
  project_id: string;
  name: string;
  description: string;
  repository_url?: string;
  active: boolean;
}

export interface ProjectList { items: Project[] }

export interface HumanReadablePolicy {
  status: "READY" | "FAILED" | "STALE" | "MISSING";
  format: "MARKDOWN";
  generator: "DETERMINISTIC_TEMPLATE";
  generator_version: string;
  content?: string;
  context_id: string;
  context_version: number;
  intent_id: string;
  intent_version: number;
  source_hash?: string;
  current_source_hash?: string;
  generated_by?: string;
  generated_at?: string;
  error?: string;
  authoritative: false;
  governance_notice: string;
}

export interface SettingsView {
  project: Project;
  current: {
    context: Record<string, unknown> & { context_id: string; version: number };
    active_intent: Record<string, unknown> & { intent_id: string; version: number };
    constraints: Array<Record<string, unknown> & { parameter_path: string; protection_level: string }>;
    context_payload: Record<string, unknown>;
    intent_payload: Record<string, unknown>;
    human_readable?: HumanReadablePolicy;
  };
  context_history: Array<{
    context_id: string;
    version: number;
    status: string;
    change_reason: string;
    confirmed_by?: string;
    effective_at?: string;
    human_readable?: HumanReadablePolicy;
  }>;
}

export interface PlanCheck {
  plan_check_id: string;
  requester_id: string;
  context_version: number;
  intent_version: number;
  check_result: "PASS" | "NEEDS_APPROVAL" | "BLOCKED";
  approval_status: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  planned_changes: unknown[];
  report: Record<string, unknown>;
  git_commit: string;
  command: string;
  created_at: string;
  allowed_actions: string[];
}

export interface Submission {
  submission_id: string;
  run_manifest_id: string;
  submitted_by: string;
  source_agent: string;
  status: string;
  workflow_status: string;
  processing_step?: string;
  processing_error?: Record<string, unknown>;
  generated_summary?: Record<string, unknown>;
  review_receipt?: Record<string, unknown>;
  risks: Array<Record<string, unknown> & { severity: string; message: string }>;
  artifacts: Array<{
    artifact_id: string;
    filename: string;
    artifact_type: string;
    size_bytes: number;
    cloud_hash_verified: boolean;
  }>;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
}

export interface Experiment {
  experiment_id: string;
  submission_id: string;
  run_manifest_id: string;
  name: string;
  model_name: string;
  dataset: string;
  protocol: string;
  seed: number;
  experiment_mode: string;
  status: string;
  context_id: string;
  context_version: number;
  intent_id: string;
  intent_version: number;
  config_hash: string;
  git_commit: string;
  summary: Record<string, unknown>;
  confirmed_at: string;
  created_at: string;
}

export interface Page<T> { items: T[]; next_cursor?: string }

export type AgentThreadStatus = "ACTIVE" | "ARCHIVED";
export type AgentRunStatus = "PENDING" | "RUNNING" | "RETRYABLE_FAILURE" | "SUCCEEDED" | "FAILED" | "DEAD_LETTER";

export interface AgentThread {
  thread_id: string;
  project_id: string;
  title: string;
  status: AgentThreadStatus;
  created_at: string;
  updated_at: string;
  archived_at?: string;
}

export interface AgentCitation {
  evidence_id: string;
  evidence_kind: AgentEvidenceKind;
  entity_type: string;
  entity_id?: string;
  entity_version?: string;
  label: string;
  excerpt: string;
}

export interface AgentMessage {
  message_id: string;
  sequence: number;
  role: "USER" | "ASSISTANT";
  content: string;
  run_id?: string;
  sections?: AgentAnswerSection[];
  citations: AgentCitation[];
  created_at: string;
}

export interface AgentAnswerSection {
  evidence_kind: AgentEvidenceKind;
  title: string;
  content: string;
  citation_ids: string[];
}

export interface AgentContextSummary {
  summary_id?: string;
  status?: "READY" | "FAILED";
  covered_sequence_from?: number;
  covered_sequence_to?: number;
  provider?: string;
  model_id?: string;
  generated_at?: string;
  degraded: boolean;
  warning?: string;
  authoritative: false;
}

export interface AgentThreadView {
  thread: AgentThread;
  messages: AgentMessage[];
  context_summary?: AgentContextSummary;
}

export interface AgentRunReceipt {
  run_id: string;
  thread_id: string;
  trigger_message_id: string;
  status: AgentRunStatus;
  events_url: string;
}

export interface AgentRun extends AgentRunReceipt {
  attempt_count: number;
  max_attempts: number;
  provider: string;
  model_id: string;
  error?: { code?: string; message?: string; retryable?: boolean };
  final_message_id?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

export type AgentEvidenceKind =
  "CONFIRMED_FACT" | "USER_PROVIDED" | "CANDIDATE_DRAFT" | "ACTION_PROPOSAL" |
  "ANALYSIS" | "HYPOTHESIS";
export type PolicyDraftStatus = "ACTIVE" | "ABANDONED";
export type PolicyDraftReadiness = "READY" | "NEEDS_CLARIFICATION" | "INVALID";

export interface PolicyContextCandidate {
  goal: string;
  non_goals: string[];
  mainline_model: string;
  baseline: Record<string, unknown>;
  dataset: string;
  protocol: string;
  primary_metric: Record<string, unknown>;
  default_seeds: number[];
  active_branch: string;
  active_config: Record<string, unknown>;
  deprecated_items: unknown[];
  key_decisions: unknown[];
  change_reason: string;
}

export interface PolicyIntentCandidate {
  name: string;
  objective: string;
  hypothesis: string;
  allowed_variables: string[];
  controlled_variables: string[];
  expected_outputs: string[];
  acceptance_criteria: string[];
  original_message: string;
}

export interface PolicyConstraintCandidate {
  parameter_path: string;
  protection_level: "LOCKED" | "APPROVAL_REQUIRED" | "EXPERIMENT_VARIABLE";
  expected_value: unknown;
  allowed_values?: unknown[];
  minimum?: number;
  maximum?: number;
  reason: string;
  original_message: string;
}

export interface PolicyDraftCandidate {
  context: PolicyContextCandidate;
  intent: PolicyIntentCandidate;
  constraints: PolicyConstraintCandidate[];
}

export interface PolicyDraftAmbiguity {
  field_path: string;
  question: string;
  source_text: string;
}

export interface PolicyDraftSummary {
  draft_id: string;
  project_id: string;
  created_by: string;
  status: PolicyDraftStatus;
  freshness: "CURRENT" | "STALE";
  base_context_id: string;
  base_context_version: number;
  base_intent_id: string;
  base_intent_version: number;
  current_revision: number;
  readiness: PolicyDraftReadiness;
  ambiguity_count: number;
  change_summary: string;
  created_at: string;
  updated_at: string;
  abandoned_at?: string;
}

export interface PolicyDraftRevisionSummary {
  revision_id: string;
  revision: number;
  author_id: string;
  source: "AGENT" | "WEB";
  readiness: PolicyDraftReadiness;
  candidate_hash: string;
  change_summary: string;
  ambiguity_count: number;
  created_at: string;
}

export interface PolicyDraftDiff {
  field_path: string;
  change_type: "ADDED" | "MODIFIED" | "REMOVED";
  previous_value?: unknown;
  candidate_value?: unknown;
  attention_level: "LOW" | "MEDIUM" | "HIGH";
  impact: string;
}

export interface PolicyDraftImpact {
  status: "COMPLETE" | "PARTIAL" | "NOT_EVALUATED";
  generated_at: string;
  pending_state_hash: string;
  attention_level: "LOW" | "MEDIUM" | "HIGH";
  future_policy_effects: string[];
  plan_simulations: Array<{
    plan_check_id: string;
    original_check_result: string;
    original_approval_status: string;
    simulated_check_result?: string;
    simulated_approval_status?: string;
    simulated_risk_codes: string[];
    changed: boolean;
    status: "SIMULATED" | "FAILED";
    error?: string;
    governance_notice: string;
  }>;
  plan_simulations_truncated: boolean;
  submission_impacts: Array<{
    submission_id: string;
    status: string;
    context_version: number;
    intent_version: number;
    classification: "IMMUTABLE_VERSION_REFERENCE";
    message: string;
  }>;
  submission_impacts_truncated: boolean;
  warnings: string[];
}

export interface PolicyDraftRevision {
  revision_id: string;
  draft_id: string;
  revision: number;
  author_id: string;
  source: "AGENT" | "WEB";
  source_run_id?: string;
  candidate: PolicyDraftCandidate;
  candidate_hash: string;
  change_summary: string;
  unresolved_ambiguities: PolicyDraftAmbiguity[];
  validation: {
    readiness: PolicyDraftReadiness;
    issues: Array<{ code: string; field_path: string; message: string }>;
    unresolved_ambiguities: PolicyDraftAmbiguity[];
  };
  diff: PolicyDraftDiff[];
  narrative: {
    status: "READY" | "FAILED";
    generator_version: string;
    source_hash: string;
    content?: string;
    error?: string;
    authoritative: false;
    governance_notice: string;
  };
  stored_impact: PolicyDraftImpact;
  current_impact: PolicyDraftImpact;
  impact_changed_since_revision: boolean;
  created_at: string;
}

export interface PolicyDraftView {
  summary: PolicyDraftSummary;
  current: PolicyDraftRevision;
  revisions: PolicyDraftRevisionSummary[];
}

interface ActionProposalBase {
  proposal_id: string;
  project_id: string;
  created_by: string;
  status: "PROPOSED" | "EXECUTED" | "CANCELED" | "STALE" | "EXPIRED" | "FAILED";
  confirmability: "READY" | "STALE" | "EXPIRED" | "TERMINAL";
  confirmability_reasons: string[];
  allowed_actions: Array<"CONFIRM" | "CANCEL">;
  source_thread_id: string;
  source_run_id: string;
  source_tool_call_id: string;
  payload_hash: string;
  base_context_id: string;
  base_context_version: number;
  base_intent_id: string;
  base_intent_version: number;
  proposal_digest: string;
  expires_at: string;
  created_at: string;
  updated_at: string;
  confirmed_by?: string;
  confirmed_at?: string;
  canceled_by?: string;
  canceled_at?: string;
  cancel_reason?: string;
  execution_result?: Record<string, unknown>;
  execution_error?: Record<string, unknown>;
}

export interface PolicyPublishActionProposal extends ActionProposalBase {
  operation: "POLICY_PUBLISH";
  source_draft_id: string;
  source_draft_revision_id: string;
  source_draft_revision: number;
  source_candidate_hash: string;
  payload: {
    expected_context_version: number;
    context: PolicyContextCandidate;
    intent: PolicyIntentCandidate;
    constraints: PolicyConstraintCandidate[];
  };
  base_policy_hash: string;
  diff_snapshot: PolicyDraftDiff[];
  impact_snapshot: PolicyDraftImpact;
  pending_state_hash: string;
  executed_context_id?: string;
  executed_context_version?: number;
}

export interface PlanDecisionActionProposal extends ActionProposalBase {
  operation: "PLAN_CHECK_DECISION";
  target_plan_check_id: string;
  target_state_hash: string;
  payload: {
    decision: "APPROVED" | "REJECTED";
    decision_reason?: string;
  };
  diff_snapshot: Array<Record<string, unknown>>;
  impact_snapshot: {
    plan_check_id: string;
    requester_id: string;
    check_result: string;
    approval_status: string;
    risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    context_version: number;
    intent_version: number;
    decision: "APPROVED" | "REJECTED";
    decision_reason?: string;
    decision_effect: string;
    risks: Array<Record<string, unknown>>;
    planned_change_count: number;
    source_report: Record<string, unknown>;
  };
  executed_approval_record_id?: string;
}

export interface SubmissionDecisionActionProposal extends ActionProposalBase {
  operation: "SUBMISSION_DECISION";
  target_submission_id: string;
  target_state_hash: string;
  payload: {
    decision: "APPROVED" | "REJECTED";
    decision_reason: string;
  };
  diff_snapshot: Array<Record<string, unknown>>;
  impact_snapshot: {
    submission_id: string;
    submitted_by: string;
    decision: "APPROVED" | "REJECTED";
    decision_reason: string;
    decision_effect: string;
    review_eligibility: "RESEARCHER_OR_OWNER" | "OWNER_ONLY" | "BLOCKED";
    highest_risk?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    objective: string;
    source_hash: string;
    review_receipt: Record<string, unknown>;
    trace: Record<string, unknown>;
    risks: Array<{
      id: string;
      risk_type: string;
      severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
      field_path?: string;
      message: string;
      impact: string;
      evidence_type?: string;
      evidence_source?: string;
      blocking: boolean;
      resolved: boolean;
      [key: string]: unknown;
    }>;
    artifacts: Array<{
      id: string;
      filename: string;
      mime_type: string;
      size_bytes: number;
      sha256: string;
      artifact_type: string;
      cloud_hash_verified: boolean;
      verified_at?: string;
      verification_evidence?: Record<string, unknown>;
      s3_version_id?: string;
      [key: string]: unknown;
    }>;
    embedding?: Record<string, unknown>;
    approval_material_complete: boolean;
    approval_material_issues: string[];
  };
  executed_approval_record_id?: string;
  executed_experiment_id?: string;
}

export type ActionProposal =
  | PolicyPublishActionProposal
  | PlanDecisionActionProposal
  | SubmissionDecisionActionProposal;
