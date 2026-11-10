-- Apply explicitly after creating the macro_data schema and the read-only role.
-- The schema migration enables RLS. Existing USAGE and SELECT grants are required.
BEGIN;

DROP POLICY IF EXISTS macro_ro_read_series ON macro_data.series;
CREATE POLICY macro_ro_read_series ON macro_data.series
    AS PERMISSIVE FOR SELECT TO marketpulse_api_ro USING (true);

DROP POLICY IF EXISTS macro_ro_read_capture ON macro_data.capture;
CREATE POLICY macro_ro_read_capture ON macro_data.capture
    AS PERMISSIVE FOR SELECT TO marketpulse_api_ro USING (true);

DROP POLICY IF EXISTS macro_ro_read_observations ON macro_data.observation_revision;
CREATE POLICY macro_ro_read_observations ON macro_data.observation_revision
    AS PERMISSIVE FOR SELECT TO marketpulse_api_ro USING (true);

DROP POLICY IF EXISTS macro_ro_read_revision_capture ON macro_data.revision_capture;
CREATE POLICY macro_ro_read_revision_capture ON macro_data.revision_capture
    AS PERMISSIVE FOR SELECT TO marketpulse_api_ro USING (true);

COMMIT;
