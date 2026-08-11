-- AI Platform Registry Schema (Phase 1) — SQLite/PostgreSQL compatible

CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'standard',
  data_residency TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  name TEXT NOT NULL,
  settings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS namespaces (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  path TEXT NOT NULL,
  env TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(project_id, path, env)
);

CREATE TABLE IF NOT EXISTS resources (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL REFERENCES namespaces(id),
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  latest_version TEXT,
  published_version TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(namespace_id, kind, name)
);

CREATE TABLE IF NOT EXISTS resource_versions (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id),
  version TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  status_json TEXT NOT NULL DEFAULT '{}',
  author_id TEXT,
  commit_message TEXT,
  bundle_hash TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(resource_id, version)
);

CREATE TABLE IF NOT EXISTS resource_dependencies (
  id TEXT PRIMARY KEY,
  resource_version_id TEXT NOT NULL REFERENCES resource_versions(id),
  depends_on_version_id TEXT NOT NULL REFERENCES resource_versions(id),
  ref_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  resource_ref TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  ip TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runtime_nodes (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL REFERENCES namespaces(id),
  node_type TEXT NOT NULL DEFAULT 'sdk',
  last_heartbeat TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resources_namespace ON resources(namespace_id);
CREATE INDEX IF NOT EXISTS idx_resource_versions_resource ON resource_versions(resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_org ON audit_events(org_id, created_at);
