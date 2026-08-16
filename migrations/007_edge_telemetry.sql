-- Edge telemetry time-series (Studio charts + health).
CREATE TABLE IF NOT EXISTS edge_telemetry_events (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  event_type TEXT NOT NULL DEFAULT 'heartbeat',
  latency_ms REAL,
  success INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}',
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edge_telemetry_node
  ON edge_telemetry_events(node_id, recorded_at);
