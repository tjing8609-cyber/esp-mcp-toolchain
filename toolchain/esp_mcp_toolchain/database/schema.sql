CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  project_id TEXT NOT NULL CHECK(length(trim(project_id)) > 0),
  run_id TEXT NOT NULL CHECK(length(trim(run_id)) > 0),
  task_type TEXT NOT NULL CHECK(length(trim(task_type)) > 0),
  status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed', 'cancelled')),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  next_sequence_no INTEGER NOT NULL DEFAULT 1 CHECK(next_sequence_no >= 1),
  selected_port TEXT,
  summary TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
    CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
  PRIMARY KEY(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS events (
  event_uuid TEXT PRIMARY KEY CHECK(
    length(event_uuid) = 36
    AND substr(event_uuid, 9, 1) = '-'
    AND substr(event_uuid, 14, 1) = '-'
    AND substr(event_uuid, 19, 1) = '-'
    AND substr(event_uuid, 24, 1) = '-'
    AND length(replace(event_uuid, '-', '')) = 32
    AND replace(event_uuid, '-', '') NOT GLOB '*[^0-9a-f]*'
    AND substr(event_uuid, 15, 1) GLOB '[1-8]'
    AND substr(event_uuid, 20, 1) GLOB '[89ab]'
  ),
  project_id TEXT NOT NULL CHECK(length(trim(project_id)) > 0),
  run_id TEXT NOT NULL CHECK(length(trim(run_id)) > 0),
  sequence_no INTEGER NOT NULL CHECK(sequence_no >= 1),
  ts TEXT NOT NULL,
  phase TEXT NOT NULL
    CHECK(phase IN ('unknown', 'prepare', 'execute', 'verify', 'cleanup', 'complete')),
  level TEXT NOT NULL
    CHECK(level IN ('debug', 'info', 'warning', 'error', 'critical')),
  tool TEXT NOT NULL CHECK(length(trim(tool)) > 0),
  source TEXT NOT NULL CHECK(length(trim(source)) > 0),
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
    CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
  UNIQUE(project_id, run_id, sequence_no),
  FOREIGN KEY(project_id, run_id)
    REFERENCES runs(project_id, run_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS legacy_jsonl_imports (
  project_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
  event_count INTEGER NOT NULL CHECK(event_count >= 0),
  imported_at TEXT NOT NULL,
  PRIMARY KEY(project_id, source_path, content_sha256)
);

CREATE TABLE IF NOT EXISTS raw_logs (
  project_id TEXT NOT NULL,
  raw_log_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sha256 TEXT,
  PRIMARY KEY(project_id, raw_log_id),
  FOREIGN KEY(project_id, run_id)
    REFERENCES runs(project_id, run_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS errors (
  project_id TEXT NOT NULL,
  error_id TEXT NOT NULL,
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
  PRIMARY KEY(project_id, error_id),
  FOREIGN KEY(project_id, run_id)
    REFERENCES runs(project_id, run_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hardwork_items (
  project_id TEXT NOT NULL,
  hardwork_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  raw_path TEXT,
  processed_path TEXT,
  source TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  PRIMARY KEY(project_id, hardwork_id)
);

CREATE TABLE IF NOT EXISTS hardwork_audit (
  project_id TEXT NOT NULL,
  audit_id TEXT NOT NULL,
  hardwork_id TEXT,
  action TEXT NOT NULL,
  old_value_json TEXT,
  new_value_json TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(project_id, audit_id)
);

CREATE TABLE IF NOT EXISTS memory_items (
  project_id TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  namespace TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  memory_type TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  PRIMARY KEY(project_id, memory_id),
  UNIQUE(project_id, namespace, key)
);

CREATE TABLE IF NOT EXISTS memory_audit (
  project_id TEXT NOT NULL,
  audit_id TEXT NOT NULL,
  memory_id TEXT,
  action TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(project_id, audit_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_project_started
  ON runs(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_project_task_status
  ON runs(project_id, task_type, status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_project_ts
  ON events(project_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_project_phase_ts
  ON events(project_id, phase, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_project_level_ts
  ON events(project_id, level, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_project_tool_ts
  ON events(project_id, tool, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_project_source_ts
  ON events(project_id, source, ts DESC);
