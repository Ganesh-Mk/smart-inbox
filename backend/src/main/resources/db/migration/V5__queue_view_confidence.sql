-- =====================================================================================
-- Smart Inbox — V5: correct the queue's headline confidence
--
-- `min_field_confidence` is the number the review queue sorts on and the number a reviewer
-- reads first, so what it counts matters.
--
-- V4 took the minimum across every live asserting field, which included the NARRATIVE row.
-- The narrative is a paragraph of prose summarising the case, not a discrete fact with its own
-- quote and its own evidence, and it carries the case-level confidence rather than a field-level
-- one. On the first full corpus run seven messages therefore reported a headline confidence of
-- 0.00 while every actual extracted fact sat between 0.85 and 0.95 — the queue was sorting the
-- best-evidenced cases to the top of the "worst first" list.
--
-- Excluding NARRATIVE makes the column mean what its name says: the least confident *fact* a
-- reviewer is being asked to sign off.
-- =====================================================================================

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

  -- The least confident *fact* awaiting sign-off. NARRATIVE is prose, not a fact, and is
  -- excluded — see the header comment.
  (SELECT MIN(f.confidence)
     FROM extracted_field f
     JOIN case_record cr ON cr.id = f.case_id
    WHERE cr.message_id = m.id
      AND f.superseded_by IS NULL
      AND f.field_group <> 'NARRATIVE'
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
