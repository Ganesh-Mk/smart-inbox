-- =====================================================================================
-- Smart Inbox — V2: indexes
--
-- Three groups, each earning its keep:
--   * the queue hot path — one composite index that the SKIP LOCKED dequeue predicate uses
--     end to end, because a queue that table-scans under four workers is not a queue;
--   * foreign keys — Oracle does not index them automatically, and an unindexed child FK
--     takes a table-level lock on the parent during DELETE;
--   * the review queue — covering the columns V_REVIEW_QUEUE aggregates over.
-- =====================================================================================


-- --- queue hot path -------------------------------------------------------------------
-- PKG_JOB_QUEUE.dequeue filters state='PENDING' AND available_at <= SYSTIMESTAMP and orders
-- by priority, id. Leading with state keeps the scan to the PENDING slice only.
CREATE INDEX ix_job_dequeue ON job (state, available_at, priority, id);
-- reap_stale_locks scans RUNNING rows by lock age (E37).
CREATE INDEX ix_job_locked ON job (state, locked_at);
-- "is every document for this message terminal?" — the E39 completion barrier.
CREATE INDEX ix_job_subject ON job (subject_type, subject_id, job_type);


-- --- foreign keys ---------------------------------------------------------------------
CREATE INDEX ix_attachment_message   ON message_attachment (message_id);
CREATE INDEX ix_document_message     ON document (message_id);
CREATE INDEX ix_document_attachment  ON document (attachment_id);
CREATE INDEX ix_page_document        ON document_page (document_id);
CREATE INDEX ix_section_document     ON document_section (document_id, page_no);
CREATE INDEX ix_table_document       ON document_table (document_id, page_no);
CREATE INDEX ix_image_document       ON document_image (document_id, page_no);
CREATE INDEX ix_summary_document     ON document_summary (document_id);
CREATE INDEX ix_validity_class       ON icsr_validity (classification_id);
CREATE INDEX ix_case_message         ON case_record (message_id);
CREATE INDEX ix_case_document        ON case_record (document_id);
CREATE INDEX ix_field_case           ON extracted_field (case_id);
CREATE INDEX ix_field_superseded     ON extracted_field (superseded_by);
CREATE INDEX ix_evidence_field       ON field_evidence (field_id);
CREATE INDEX ix_evidence_document    ON field_evidence (document_id, page_no);
CREATE INDEX ix_review_message       ON review_decision (message_id);
CREATE INDEX ix_class_superseded     ON classification (superseded_by);
CREATE INDEX ix_lit_item_batch       ON literature_item (batch_id);
CREATE INDEX ix_lit_item_document    ON literature_item (document_id);


-- --- E9: content-addressed parse cache -------------------------------------------------
-- "have we already parsed this exact PDF?" The second copy of an attachment costs zero
-- LLM calls, which is real money saved on a batch run.
CREATE INDEX ix_document_sha         ON document (content_sha256);
CREATE INDEX ix_attachment_sha       ON message_attachment (sha256);


-- --- review queue ----------------------------------------------------------------------
-- The queue screen sorts worst-first: needs-attention, then lowest confidence, then oldest.
CREATE INDEX ix_message_queue        ON inbox_message (status, needs_attention, received_at);
CREATE INDEX ix_message_received     ON inbox_message (received_at DESC);
-- Roll-up of live (non-superseded) labels per subject, which is what the view joins on.
CREATE INDEX ix_class_subject_live   ON classification (subject_type, subject_id, superseded_by);
-- Unverified-evidence count per message, the amber flag in the queue (E27).
CREATE INDEX ix_evidence_verified    ON field_evidence (verified);


-- --- audit and metrics ------------------------------------------------------------------
CREATE INDEX ix_audit_entity         ON audit_event (entity_type, entity_id, occurred_at);
CREATE INDEX ix_audit_message        ON audit_event (message_id, occurred_at);
CREATE INDEX ix_metric_subject       ON processing_metric (subject_type, subject_id, stage);
CREATE INDEX ix_ai_call_job          ON ai_call_log (job_id);
CREATE INDEX ix_ai_call_created      ON ai_call_log (created_at);
