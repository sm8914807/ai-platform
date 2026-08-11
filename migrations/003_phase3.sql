-- Phase 3: marketplace, git sync, identity (SSO/SCIM)

CREATE TABLE IF NOT EXISTS marketplace_plugins (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  plugin_type TEXT NOT NULL,
  author TEXT,
  tier TEXT NOT NULL DEFAULT 'community',
  manifest_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'published',
  created_at TEXT NOT NULL,
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS marketplace_installations (
  id TEXT PRIMARY KEY,
  plugin_id TEXT NOT NULL REFERENCES marketplace_plugins(id),
  namespace_id TEXT NOT NULL,
  installed_by TEXT,
  installed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_sync_repos (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT 'main',
  last_sync_at TEXT,
  last_commit TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_users (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  email TEXT NOT NULL,
  display_name TEXT,
  external_id TEXT,
  teams_json TEXT NOT NULL DEFAULT '[]',
  active BOOLEAN NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(org_id, email)
);

CREATE TABLE IF NOT EXISTS identity_teams (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(org_id, name)
);

CREATE TABLE IF NOT EXISTS sso_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES identity_users(id),
  provider TEXT NOT NULL,
  token_hash TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plugins_tier ON marketplace_plugins(tier);
CREATE INDEX IF NOT EXISTS idx_users_org ON identity_users(org_id);
CREATE INDEX IF NOT EXISTS idx_git_sync_ns ON git_sync_repos(namespace_id);
