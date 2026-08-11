-- Phase 5: Context Graph, Agent Discovery, Dynamic Workflows

CREATE TABLE IF NOT EXISTS decision_traces (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  workflow_id TEXT,
  agent_ref TEXT NOT NULL,
  trace_type TEXT NOT NULL DEFAULT 'decision',
  entities_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  visibility TEXT NOT NULL DEFAULT 'workflow',
  payload_json TEXT NOT NULL DEFAULT '{}',
  outcome TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_trace_links (
  id TEXT PRIMARY KEY,
  from_trace_id TEXT NOT NULL REFERENCES decision_traces(id),
  to_trace_id TEXT NOT NULL REFERENCES decision_traces(id),
  link_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(from_trace_id, to_trace_id, link_type)
);

CREATE TABLE IF NOT EXISTS agent_capabilities (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  agent_ref TEXT NOT NULL,
  address TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  schemas_json TEXT NOT NULL DEFAULT '[]',
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  status TEXT NOT NULL DEFAULT 'online',
  last_active TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(namespace_id, agent_ref)
);

CREATE TABLE IF NOT EXISTS dynamic_workflows (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  plan_json TEXT NOT NULL,
  ir_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_traces_ns ON decision_traces(namespace_id);
CREATE INDEX IF NOT EXISTS idx_traces_agent ON decision_traces(agent_ref);
CREATE INDEX IF NOT EXISTS idx_traces_workflow ON decision_traces(workflow_id);
CREATE INDEX IF NOT EXISTS idx_caps_ns ON agent_capabilities(namespace_id);
CREATE INDEX IF NOT EXISTS idx_dyn_wf_ns ON dynamic_workflows(namespace_id);
