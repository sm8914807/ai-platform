-- Phase 6: Inter-agent message bus

CREATE TABLE IF NOT EXISTS agent_messages (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  recipient TEXT NOT NULL,
  subject TEXT,
  schema_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  idempotency_key TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  error_json TEXT,
  created_at TEXT NOT NULL,
  delivered_at TEXT,
  acked_at TEXT,
  UNIQUE(namespace_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS agent_inboxes (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  agent_address TEXT NOT NULL,
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  webhook_url TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(namespace_id, agent_address)
);

CREATE INDEX IF NOT EXISTS idx_messages_inbox ON agent_messages(namespace_id, recipient, status);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON agent_messages(namespace_id, sender);
