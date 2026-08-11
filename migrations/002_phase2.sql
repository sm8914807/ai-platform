-- Phase 2: workflows, memory, knowledge, evaluation, environments

CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  workflow_version_id TEXT NOT NULL,
  org_id TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  completed_at TEXT,
  checkpoint_seq INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workflow_step_runs (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  step_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 1,
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT,
  completed_at TEXT,
  error_json TEXT
);

CREATE TABLE IF NOT EXISTS workflow_checkpoints (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  seq INTEGER NOT NULL,
  state_blob_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(workflow_run_id, seq)
);

CREATE TABLE IF NOT EXISTS memory_entries (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  layer TEXT NOT NULL,
  content_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_snapshots (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  version INTEGER NOT NULL,
  entries_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding_json TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
  id TEXT PRIMARY KEY,
  suite_name TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  target_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  scores_json TEXT NOT NULL DEFAULT '{}',
  passed BOOLEAN NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS evaluation_results (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
  case_id TEXT NOT NULL,
  scores_json TEXT NOT NULL DEFAULT '{}',
  trace_ref TEXT
);

CREATE TABLE IF NOT EXISTS environment_promotions (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  from_env TEXT NOT NULL,
  to_env TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  requested_by TEXT,
  approved_by TEXT,
  bundle_hash TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_ns ON workflow_runs(namespace_id);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(scope, layer);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_target ON evaluation_runs(target_ref, target_version);
