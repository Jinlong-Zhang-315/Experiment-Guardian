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
}

export interface Project {
  project_id: string;
  name: string;
  description: string;
  repository_url?: string;
  active: boolean;
}

export interface ProjectList { items: Project[] }

export interface SettingsView {
  project: Project;
  current: {
    context: Record<string, unknown> & { context_id: string; version: number };
    active_intent: Record<string, unknown> & { intent_id: string; version: number };
    constraints: Array<Record<string, unknown> & { parameter_path: string; protection_level: string }>;
    context_payload: Record<string, unknown>;
    intent_payload: Record<string, unknown>;
  };
  context_history: Array<{
    context_id: string;
    version: number;
    status: string;
    change_reason: string;
    confirmed_by?: string;
    effective_at?: string;
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
