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
  evidence_kind: "CONFIRMED_FACT" | "USER_PROVIDED" | "ANALYSIS" | "HYPOTHESIS";
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
  citations: AgentCitation[];
  created_at: string;
}

export interface AgentThreadView {
  thread: AgentThread;
  messages: AgentMessage[];
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
