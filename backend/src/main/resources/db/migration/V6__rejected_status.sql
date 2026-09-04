-- A rejected message must not read as an accepted one, and a reviewer's note must survive
-- into the audit trail.
--
-- pkg_review.apply_override set status = 'REVIEWED' for every decision, so ACCEPT, OVERRIDE and
-- REJECT became indistinguishable once the screen refreshed: the queue showed "reviewed" for a
-- message a human had explicitly thrown out. The decision was recorded correctly in
-- REVIEW_DECISION and in the audit trail, but the status a reviewer actually reads was wrong,
-- and in a pharmacovigilance queue "somebody dealt with this" and "somebody rejected this" are
-- not the same claim.
--
-- The audit row's after_json also carried only the category list, so the note typed into a box
-- captioned "recorded in the audit trail" was stored in REVIEW_DECISION and shown nowhere.
--
-- Existing rows keep the status they were given. The audit is append-only and a migration must
-- not restate history it did not witness.
--
-- The package body below is V3's, with only the three changes described above.

ALTER TABLE inbox_message DROP CONSTRAINT ck_inbox_message_status;

ALTER TABLE inbox_message ADD CONSTRAINT ck_inbox_message_status CHECK (status IN (
  'RECEIVED','PARSING','PARSED','CLASSIFYING','CLASSIFIED','EXTRACTING',
  'READY_FOR_REVIEW','REVIEWED','REJECTED','NEEDS_ATTENTION','FAILED'));

CREATE OR REPLACE PACKAGE BODY pkg_review AS

  PROCEDURE apply_override(
    p_message_id IN NUMBER,
    p_reviewer   IN VARCHAR2,
    p_categories IN VARCHAR2,
    p_decision   IN VARCHAR2,
    p_notes      IN VARCHAR2 DEFAULT NULL,
    p_corr       IN VARCHAR2 DEFAULT NULL
  ) IS
    v_before CLOB;
    v_new_id NUMBER;
    v_status VARCHAR2(30);
  BEGIN
    -- Snapshot the live AI labels before anything changes, for the audit record.
    SELECT JSON_ARRAYAGG(category ORDER BY category RETURNING CLOB)
      INTO v_before
      FROM classification
     WHERE subject_type = 'MESSAGE'
       AND subject_id = p_message_id
       AND superseded_by IS NULL;

    IF p_decision = 'OVERRIDE' THEN
      -- One new REVIEWER row per chosen category, each superseding nothing by itself; the
      -- old rows are then pointed at the *first* new row so the chain is navigable.
      FOR c IN (
        SELECT category_value
          FROM JSON_TABLE(p_categories, '$[*]'
                 COLUMNS (category_value VARCHAR2(30) PATH '$'))
      ) LOOP
        INSERT INTO classification (subject_type, subject_id, category, confidence,
                                    reason, decided_by, decided_by_user)
        VALUES ('MESSAGE', p_message_id, c.category_value, 1,
                'Set by reviewer', 'REVIEWER', p_reviewer)
        RETURNING id INTO v_new_id;
      END LOOP;

      UPDATE classification
         SET superseded_by = v_new_id
       WHERE subject_type = 'MESSAGE'
         AND subject_id = p_message_id
         AND superseded_by IS NULL
         AND decided_by <> 'REVIEWER';
    END IF;

    INSERT INTO review_decision (message_id, reviewer, decision,
                                 final_categories_json, notes)
    VALUES (p_message_id, p_reviewer, p_decision, p_categories, p_notes);

    -- REJECT must not leave the message reading as accepted (V6).
    v_status := CASE WHEN p_decision = 'REJECT' THEN 'REJECTED' ELSE 'REVIEWED' END;

    UPDATE inbox_message
       SET status = v_status,
           updated_at = SYSTIMESTAMP
     WHERE id = p_message_id;

    pkg_audit.log(
      p_actor       => p_reviewer,
      p_actor_type  => 'REVIEWER',
      p_action      => 'REVIEW_' || p_decision,
      p_entity_type => 'MESSAGE',
      p_entity_id   => p_message_id,
      p_before      => v_before,
      p_after       => TO_CLOB(JSON_OBJECT('decision'   VALUE p_decision,
                                           'categories' VALUE p_categories FORMAT JSON,
                                           'status'     VALUE v_status,
                                           'notes'      VALUE p_notes)),
      p_corr        => p_corr,
      p_message_id  => p_message_id
    );
  END apply_override;


  PROCEDURE override_field(
    p_field_id   IN NUMBER,
    p_reviewer   IN VARCHAR2,
    p_value      IN VARCHAR2,
    p_status     IN VARCHAR2,
    p_note       IN VARCHAR2 DEFAULT NULL,
    p_corr       IN VARCHAR2 DEFAULT NULL,
    p_new_id     OUT NUMBER
  ) IS
    v_old   extracted_field%ROWTYPE;
    v_before CLOB;
    v_after  CLOB;
    v_msg_id NUMBER;
  BEGIN
    SELECT * INTO v_old FROM extracted_field WHERE id = p_field_id FOR UPDATE;

    v_before := TO_CLOB(JSON_OBJECT(
      'value' VALUE v_old.value_text,
      'status' VALUE v_old.status,
      'confidence' VALUE v_old.confidence,
      'decided_by' VALUE v_old.decided_by));

    -- The AI row is NEVER edited. A new REVIEWER row is inserted and the old one points at it.
    INSERT INTO extracted_field (
      case_id, field_group, field_path, field_index, value_text, value_json, value_en,
      unit, raw_text, status, confidence, confidence_pre_adjust, adjust_reason,
      decided_by, decided_by_user)
    VALUES (
      v_old.case_id, v_old.field_group, v_old.field_path, v_old.field_index,
      p_value, v_old.value_json, v_old.value_en, v_old.unit, v_old.raw_text,
      p_status, 1, v_old.confidence,
      SUBSTR('Reviewer override. ' || NVL(p_note, ''), 1, 500),
      'REVIEWER', p_reviewer)
    RETURNING id INTO p_new_id;

    UPDATE extracted_field SET superseded_by = p_new_id WHERE id = p_field_id;

    v_after := TO_CLOB(JSON_OBJECT(
      'value' VALUE p_value,
      'status' VALUE p_status,
      'confidence' VALUE 1,
      'decided_by' VALUE 'REVIEWER',
      'note' VALUE p_note));

    SELECT c.message_id INTO v_msg_id
      FROM case_record c WHERE c.id = v_old.case_id;

    pkg_audit.log(
      p_actor       => p_reviewer,
      p_actor_type  => 'REVIEWER',
      p_action      => 'FIELD_OVERRIDE',
      p_entity_type => 'EXTRACTED_FIELD',
      p_entity_id   => p_field_id,
      p_before      => v_before,
      p_after       => v_after,
      p_corr        => p_corr,
      p_message_id  => v_msg_id
    );
  END override_field;

END pkg_review;
/
