-- AC-338: Persist per-generation dimensional scoring summaries for trajectory/reporting.
ALTER TABLE generations ADD COLUMN dimension_summary_json TEXT DEFAULT NULL;
