-- Phase 4: multi-region, edge runtimes, route metrics, compliance packs

CREATE TABLE IF NOT EXISTS regions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  endpoint TEXT NOT NULL,
  data_residency TEXT,
  is_primary BOOLEAN NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edge_runtimes (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  region_id TEXT,
  node_type TEXT NOT NULL DEFAULT 'edge',
  bundle_hash TEXT,
  bundle_cache_path TEXT,
  last_sync_at TEXT,
  last_telemetry_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'online',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_route_metrics (
  id TEXT PRIMARY KEY,
  route_name TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  latency_ms REAL NOT NULL,
  success BOOLEAN NOT NULL,
  cost_units REAL NOT NULL DEFAULT 0,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_tuning_runs (
  id TEXT PRIMARY KEY,
  route_name TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  old_weights_json TEXT NOT NULL,
  new_weights_json TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compliance_installations (
  id TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL,
  namespace_id TEXT NOT NULL,
  installed_by TEXT,
  installed_at TEXT NOT NULL,
  UNIQUE(pack_id, namespace_id)
);

CREATE INDEX IF NOT EXISTS idx_route_metrics_route ON model_route_metrics(route_name, namespace_id);
CREATE INDEX IF NOT EXISTS idx_edge_runtimes_ns ON edge_runtimes(namespace_id);
CREATE INDEX IF NOT EXISTS idx_regions_status ON regions(status);
