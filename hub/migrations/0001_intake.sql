PRAGMA foreign_keys = ON;

CREATE TABLE tokens (
  hash TEXT PRIMARY KEY CHECK(length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
  org TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('read','submit','operator')),
  expires INTEGER NOT NULL,
  lineage_id TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
  created INTEGER NOT NULL
);
CREATE INDEX tokens_lineage ON tokens(org, lineage_id, revoked, expires);

CREATE TABLE intake (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL REFERENCES tokens(hash),
  lineage_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('procedure','lesson')),
  service_id TEXT NOT NULL,
  target_note_id TEXT,
  expected_revision TEXT,
  body TEXT,
  doc_url TEXT,
  success_evidence TEXT,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending','held','committed','published','rejected')),
  reason_code TEXT,
  note_id TEXT,
  revision TEXT,
  gate_digest TEXT,
  commit_sha TEXT,
  published_commit TEXT,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  day INTEGER NOT NULL,
  UNIQUE(lineage_id, request_id),
  CHECK((target_note_id IS NULL) = (expected_revision IS NULL)),
  CHECK(body IS NULL OR length(CAST(body AS BLOB)) <= 2048),
  CHECK(doc_url IS NULL OR length(CAST(doc_url AS BLOB)) <= 2048),
  CHECK(success_evidence IS NULL OR json_valid(success_evidence)),
  CHECK(state <> 'published' OR (published_commit IS NOT NULL AND revision IS NOT NULL))
);
CREATE INDEX intake_daily ON intake(token_hash, day);
CREATE INDEX intake_queue ON intake(state, created, id);
CREATE INDEX intake_revision ON intake(note_id, revision);

CREATE TABLE events (
  id TEXT PRIMARY KEY,
  intake_id TEXT REFERENCES intake(id),
  note_id TEXT,
  revision TEXT,
  lineage_id TEXT NOT NULL,
  token_hash TEXT NOT NULL REFERENCES tokens(hash),
  kind TEXT NOT NULL CHECK(kind IN ('confirm','report','ack','cost','reservation')),
  action TEXT NOT NULL DEFAULT '',
  operation_id TEXT NOT NULL,
  value INTEGER NOT NULL DEFAULT 0 CHECK(value >= 0),
  payload TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload)),
  month TEXT,
  created INTEGER NOT NULL,
  day INTEGER NOT NULL,
  UNIQUE(kind, operation_id),
  CHECK(kind IN ('cost','reservation') OR intake_id IS NOT NULL OR (note_id IS NOT NULL AND revision IS NOT NULL)),
  CHECK(kind NOT IN ('confirm','report') OR (note_id IS NOT NULL AND revision IS NOT NULL)),
  CHECK(kind NOT IN ('cost','reservation') OR month IS NOT NULL)
);
CREATE UNIQUE INDEX event_vote ON events(note_id, revision, lineage_id, kind)
  WHERE kind IN ('confirm','report');
CREATE INDEX events_daily ON events(token_hash, kind, day);
CREATE INDEX events_revision ON events(note_id, revision, kind);
CREATE INDEX events_budget ON events(month, kind);

CREATE TABLE budget_override (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  cap_microusd INTEGER NOT NULL CHECK(cap_microusd >= 0),
  updated INTEGER NOT NULL
);
