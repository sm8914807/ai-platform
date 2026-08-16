-- Aux stores for multi-tenant SaaS (workflows, messaging, traces, identity, …)

CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  workflow_version_id TEXT NOT NULL,
  org_id TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  input_json JSONB NOT NULL DEFAULT '{}',
  output_json JSONB NOT NULL DEFAULT '{}',
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  checkpoint_seq INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workflow_step_runs (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  step_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 1,
  input_json JSONB NOT NULL DEFAULT '{}',
  output_json JSONB NOT NULL DEFAULT '{}',
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_json JSONB
);

CREATE TABLE IF NOT EXISTS workflow_checkpoints (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  seq INTEGER NOT NULL,
  state_blob_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(workflow_run_id, seq)
);

CREATE TABLE IF NOT EXISTS marketplace_plugins (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  plugin_type TEXT NOT NULL,
  author TEXT,
  tier TEXT NOT NULL DEFAULT 'community',
  manifest_json JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'published',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS marketplace_installations (
  id TEXT PRIMARY KEY,
  plugin_id TEXT NOT NULL REFERENCES marketplace_plugins(id),
  namespace_id TEXT NOT NULL,
  installed_by TEXT,
  installed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS git_sync_repos (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT 'main',
  last_sync_at TIMESTAMPTZ,
  last_commit TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS identity_users (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  email TEXT NOT NULL,
  display_name TEXT,
  external_id TEXT,
  teams_json JSONB NOT NULL DEFAULT '[]',
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(org_id, email)
);

CREATE TABLE IF NOT EXISTS identity_teams (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(org_id, name)
);

CREATE TABLE IF NOT EXISTS sso_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES identity_users(id),
  provider TEXT NOT NULL,
  token_hash TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS regions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  endpoint TEXT NOT NULL,
  data_residency TEXT,
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS edge_runtimes (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  region_id TEXT,
  node_type TEXT NOT NULL DEFAULT 'edge',
  bundle_hash TEXT,
  bundle_cache_path TEXT,
  last_sync_at TIMESTAMPTZ,
  last_telemetry_at TIMESTAMPTZ,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'online',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_route_metrics (
  id TEXT PRIMARY KEY,
  route_name TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  latency_ms DOUBLE PRECISION NOT NULL,
  success BOOLEAN NOT NULL,
  cost_units DOUBLE PRECISION NOT NULL DEFAULT 0,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS route_tuning_runs (
  id TEXT PRIMARY KEY,
  route_name TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  old_weights_json JSONB NOT NULL,
  new_weights_json JSONB NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS compliance_installations (
  id TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  installed_by TEXT,
  installed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(pack_id, namespace_id)
);

CREATE TABLE IF NOT EXISTS decision_traces (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  workflow_id TEXT,
  agent_ref TEXT NOT NULL,
  trace_type TEXT NOT NULL DEFAULT 'decision',
  entities_json JSONB NOT NULL DEFAULT '[]',
  tags_json JSONB NOT NULL DEFAULT '[]',
  visibility TEXT NOT NULL DEFAULT 'workflow',
  payload_json JSONB NOT NULL DEFAULT '{}',
  outcome TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decision_trace_links (
  id TEXT PRIMARY KEY,
  from_trace_id TEXT NOT NULL REFERENCES decision_traces(id),
  to_trace_id TEXT NOT NULL REFERENCES decision_traces(id),
  link_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(from_trace_id, to_trace_id, link_type)
);

CREATE TABLE IF NOT EXISTS agent_capabilities (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  agent_ref TEXT NOT NULL,
  address TEXT,
  capabilities_json JSONB NOT NULL DEFAULT '[]',
  schemas_json JSONB NOT NULL DEFAULT '[]',
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  status TEXT NOT NULL DEFAULT 'online',
  last_active TIMESTAMPTZ,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(namespace_id, agent_ref)
);

CREATE TABLE IF NOT EXISTS dynamic_workflows (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  plan_json JSONB NOT NULL,
  ir_json JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  input_json JSONB NOT NULL DEFAULT '{}',
  output_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_messages (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  recipient TEXT NOT NULL,
  subject TEXT,
  schema_id TEXT,
  payload_json JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  idempotency_key TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  error_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  delivered_at TIMESTAMPTZ,
  acked_at TIMESTAMPTZ,
  UNIQUE(namespace_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS agent_inboxes (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  agent_address TEXT NOT NULL,
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  webhook_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(namespace_id, agent_address)
);

CREATE TABLE IF NOT EXISTS secrets (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  name TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  rotated_at TIMESTAMPTZ,
  UNIQUE(namespace_id, name)
);

CREATE TABLE IF NOT EXISTS memory_entries (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  layer TEXT NOT NULL,
  content_json JSONB NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS memory_snapshots (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  version INTEGER NOT NULL,
  entries_json JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding_json JSONB,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(scope, layer);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_chunks(source_name);

CREATE TABLE IF NOT EXISTS amtp_schemas (
  id TEXT PRIMARY KEY,
  schema_id TEXT NOT NULL UNIQUE,
  version TEXT NOT NULL DEFAULT '1.0',
  definition_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amtp_delivery_status (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 0,
  error_json JSONB,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(message_id, recipient)
);

CREATE TABLE IF NOT EXISTS amtp_agents (
  id TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  address TEXT NOT NULL,
  api_key_hash TEXT,
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  push_target TEXT,
  supported_schemas_json JSONB NOT NULL DEFAULT '[]',
  active BOOLEAN NOT NULL DEFAULT TRUE,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(domain, address)
);

CREATE INDEX IF NOT EXISTS idx_messages_inbox ON agent_messages(namespace_id, recipient, status);
CREATE INDEX IF NOT EXISTS idx_traces_ns ON decision_traces(namespace_id);
CREATE INDEX IF NOT EXISTS idx_caps_ns ON agent_capabilities(namespace_id);
CREATE INDEX IF NOT EXISTS idx_route_metrics_route ON model_route_metrics(route_name, namespace_id);
CREATE INDEX IF NOT EXISTS idx_amtp_status_msg ON amtp_delivery_status(message_id);

CREATE TABLE IF NOT EXISTS edge_telemetry_events (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  event_type TEXT NOT NULL DEFAULT 'heartbeat',
  latency_ms DOUBLE PRECISION,
  success BOOLEAN,
  payload_json JSONB NOT NULL DEFAULT '{}',
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_edge_telemetry_node
  ON edge_telemetry_events(node_id, recorded_at);

