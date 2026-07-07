CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL,
  summary TEXT,
  selected_port TEXT,
  project_dir TEXT
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  tool TEXT,
  level TEXT,
  source TEXT,
  message TEXT,
  data_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS raw_logs (
  raw_log_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sha256 TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS errors (
  error_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  error_kind TEXT NOT NULL,
  file TEXT,
  line INTEGER,
  column INTEGER,
  exception_type TEXT,
  message TEXT,
  raw_text TEXT,
  recoverable INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS hardwork_items (
  hardwork_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  raw_path TEXT,
  processed_path TEXT,
  source TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS hardwork_audit (
  audit_id TEXT PRIMARY KEY,
  hardwork_id TEXT,
  action TEXT NOT NULL,
  old_value_json TEXT,
  new_value_json TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
  memory_id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  memory_type TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  UNIQUE(namespace, key)
);

CREATE TABLE IF NOT EXISTS memory_audit (
  audit_id TEXT PRIMARY KEY,
  memory_id TEXT,
  action TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
);

