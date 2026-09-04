-- =====================================================================================
-- Smart Inbox — V4: triggers and the review-queue view
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- updated_at maintenance. Cheap, and it means a row's last-touched time is never a lie
-- told by a forgetful UPDATE statement.
-- -------------------------------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_inbox_message_touch
  BEFORE UPDATE ON inbox_message
  FOR EACH ROW
BEGIN
  :NEW.updated_at := SYSTIMESTAMP;
END;
/

CREATE OR REPLACE TRIGGER trg_job_touch
  BEFORE UPDATE ON job
  FOR EACH ROW
BEGIN
  :NEW.updated_at := SYSTIMESTAMP;
END;
/


-- -------------------------------------------------------------------------------------
-- Audit safety net (PROJECT_PLAN §9.4).
--
-- Scope, chosen deliberately (see DECISIONS D-008):
--   * every UPDATE is audited. The schema is append-only by design, so an in-place change to
--     a classification or an extracted field is precisely the anomaly that must never happen
--     silently — including a supersede, which is the one legitimate update.
--   * INSERTs are audited only when decided_by = 'REVIEWER'. A human action is always an
--     audit event. The AI's own inserts are already fully reproducible from AI_CALL_LOG, and
--     auditing each of the ~40 fields per case would bury the reviewer's timeline in noise.
--
-- PKG_AUDIT.log runs in an autonomous transaction, so these rows survive a rollback of the
-- statement that fired them.
-- -------------------------------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_classification_audit
  AFTER INSERT OR UPDATE ON classification
  FOR EACH ROW
DECLARE
  v_before CLOB;
  v_after  CLOB;
BEGIN
  IF INSERTING AND :NEW.decided_by <> 'REVIEWER' THEN
    RETURN;
  END IF;

  IF UPDATING THEN
    v_before := TO_CLOB(JSON_OBJECT(
      'category'      VALUE :OLD.category,
      'confidence'    VALUE :OLD.confidence,
      'decided_by'    VALUE :OLD.decided_by,
      'superseded_by' VALUE :OLD.superseded_by));
  END IF;

  v_after := TO_CLOB(JSON_OBJECT(
    'category'      VALUE :NEW.category,
    'confidence'    VALUE :NEW.confidence,
    'decided_by'    VALUE :NEW.decided_by,
    'superseded_by' VALUE :NEW.superseded_by));

  pkg_audit.log(
    p_actor       => NVL(:NEW.decided_by_user, 'system'),
    p_actor_type  => CASE WHEN :NEW.decided_by = 'REVIEWER' THEN 'REVIEWER' ELSE 'SYSTEM' END,
    p_action      => CASE WHEN INSERTING THEN 'CLASSIFICATION_INSERT'
                          ELSE 'CLASSIFICATION_UPDATE' END,
    p_entity_type => 'CLASSIFICATION',
    p_entity_id   => :NEW.id,
    p_before      => v_before,
    p_after       => v_after,
    p_message_id  => CASE WHEN :NEW.subject_type = 'MESSAGE' THEN :NEW.subject_id END
  );
END;
/


CREATE OR REPLACE TRIGGER trg_field_audit
  AFTER INSERT OR UPDATE ON extracted_field
  FOR EACH ROW
DECLARE
  v_before CLOB;
  v_after  CLOB;
BEGIN
  IF INSERTING AND :NEW.decided_by <> 'REVIEWER' THEN
    RETURN;
  END IF;

  IF UPDATING THEN
    v_before := TO_CLOB(JSON_OBJECT(
      'field_path'    VALUE :OLD.field_path,
      'value'         VALUE :OLD.value_text,
      'status'        VALUE :OLD.status,
      'confidence'    VALUE :OLD.confidence,
      'decided_by'    VALUE :OLD.decided_by,
      'superseded_by' VALUE :OLD.superseded_by));
  END IF;

  v_after := TO_CLOB(JSON_OBJECT(
    'field_path'    VALUE :NEW.field_path,
    'value'         VALUE :NEW.value_text,
    'status'        VALUE :NEW.status,
    'confidence'    VALUE :NEW.confidence,
    'decided_by'    VALUE :NEW.decided_by,
    'superseded_by' VALUE :NEW.superseded_by));

  pkg_audit.log(
    p_actor       => NVL(:NEW.decided_by_user, 'system'),
    p_actor_type  => CASE WHEN :NEW.decided_by = 'REVIEWER' THEN 'REVIEWER' ELSE 'SYSTEM' END,
    p_action      => CASE WHEN INSERTING THEN 'FIELD_INSERT' ELSE 'FIELD_UPDATE' END,
    p_entity_type => 'EXTRACTED_FIELD',
    p_entity_id   => :NEW.id,
    p_before      => v_before,
    p_after       => v_after
  );
END;
/


-- -------------------------------------------------------------------------------------
-- V_REVIEW_QUEUE — what the reviewer's day actually looks like.
--
-- The queue screen reads this one view rather than five joins. Ordered worst-first:
-- anything needing attention, then the lowest field confidence, then the oldest message.
-- The three flag columns are the amber badges in the UI:
--   unverified_evidence  a fact the model cited but code could not find in the source (E27)
--   conflict_count       body and attachment disagree; the reviewer must choose (E33)
--   truncated_docs       an oversized PDF processed only to the page cap (E8)
-- -------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_review_queue AS
SELECT
  m.id                                   AS message_id,
  m.sender_email,
  m.sender_name,
  m.subject,
  m.sent_at,
  m.received_at,
  m.status,
  m.needs_attention,
  m.attention_reason,

  -- Live (non-superseded) message-level labels, e.g. "ICSR,PQC".
  (SELECT LISTAGG(c.category, ',') WITHIN GROUP (ORDER BY c.category)
     FROM classification c
    WHERE c.subject_type = 'MESSAGE'
      AND c.subject_id = m.id
      AND c.superseded_by IS NULL)       AS categories,

  (SELECT MAX(c.confidence)
     FROM classification c
    WHERE c.subject_type = 'MESSAGE'
      AND c.subject_id = m.id
      AND c.superseded_by IS NULL)       AS top_category_confidence,

  (SELECT COUNT(*)
     FROM document d
    WHERE d.message_id = m.id)           AS document_count,

  (SELECT COUNT(*)
     FROM document d
    WHERE d.message_id = m.id
      AND d.parse_status = 'PARSE_FAILED') AS failed_document_count,

  (SELECT COUNT(*)
     FROM document d
    WHERE d.message_id = m.id
      AND d.truncated = 'Y')             AS truncated_docs,

  (SELECT COUNT(*)
     FROM case_record cr
    WHERE cr.message_id = m.id)          AS case_count,

  -- Lowest confidence among the facts a human would actually be asked to sign off:
  -- live rows only, and only those that assert something.
  (SELECT MIN(f.confidence)
     FROM extracted_field f
     JOIN case_record cr ON cr.id = f.case_id
    WHERE cr.message_id = m.id
      AND f.superseded_by IS NULL
      AND f.status IN ('STATED','UNCERTAIN','CONFLICT')) AS min_field_confidence,

  (SELECT COUNT(*)
     FROM field_evidence e
     JOIN extracted_field f ON f.id = e.field_id
     JOIN case_record cr ON cr.id = f.case_id
    WHERE cr.message_id = m.id
      AND f.superseded_by IS NULL
      AND e.verified = 'N')              AS unverified_evidence,

  (SELECT COUNT(*)
     FROM extracted_field f
     JOIN case_record cr ON cr.id = f.case_id
    WHERE cr.message_id = m.id
      AND f.superseded_by IS NULL
      AND f.status = 'CONFLICT')         AS conflict_count,

  (SELECT MAX(rd.decided_at)
     FROM review_decision rd
    WHERE rd.message_id = m.id)          AS last_reviewed_at,

  (SELECT COUNT(*)
     FROM job j
    WHERE j.subject_type = 'MESSAGE'
      AND j.subject_id = m.id
      AND j.state = 'DEAD')              AS dead_jobs

FROM inbox_message m;

COMMENT ON TABLE v_review_queue IS
  'Reviewer work queue. Sort worst-first: needs_attention DESC, min_field_confidence ASC, received_at ASC.';
