-- Apply after 009. New reports persist exactly one immutable CAMSnapshot.
-- Existing archived reports remain unmodified; NULL means legacy format.
ALTER TABLE dpr_report_artifacts
    ADD COLUMN IF NOT EXISTS cam_snapshot JSONB,
    ADD COLUMN IF NOT EXISTS cam_schema_version TEXT,
    ADD COLUMN IF NOT EXISTS cam_snapshot_sha256 TEXT;

-- Strengthen the existing immutable trigger without replacing its PDF
-- recovery permissions. The original trigger remains responsible for the
-- pre-existing report / PDF lifecycle checks.
CREATE OR REPLACE FUNCTION dpr_cam_snapshot_immutable() RETURNS trigger AS $$
BEGIN
  IF OLD.cam_snapshot IS DISTINCT FROM NEW.cam_snapshot
     OR OLD.cam_schema_version IS DISTINCT FROM NEW.cam_schema_version
     OR OLD.cam_snapshot_sha256 IS DISTINCT FROM NEW.cam_snapshot_sha256 THEN
    RAISE EXCEPTION 'Canonical CAM snapshot is immutable (version=%)', OLD.version;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_dpr_cam_snapshot_immutable ON dpr_report_artifacts;
CREATE TRIGGER trg_dpr_cam_snapshot_immutable
  BEFORE UPDATE ON dpr_report_artifacts
  FOR EACH ROW EXECUTE FUNCTION dpr_cam_snapshot_immutable();
