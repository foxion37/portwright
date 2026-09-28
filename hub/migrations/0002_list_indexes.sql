-- Operator list pages walk events and intake in (created, id) order, optionally by kind.
-- Without these indexes every page read the whole table (D1 free-tier row reads, 2026-09-28).
CREATE INDEX IF NOT EXISTS events_created ON events(created, id);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, created, id);
CREATE INDEX IF NOT EXISTS intake_created ON intake(created, id);
