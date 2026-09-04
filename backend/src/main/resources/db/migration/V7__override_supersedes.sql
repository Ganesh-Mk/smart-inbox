-- An override must replace the previous labels, not add to them.
--
-- `apply_override` superseded live classifications with `decided_by <> 'REVIEWER'`. That guard
-- existed to avoid superseding the rows the same call had just inserted, but it also spared
-- every label an *earlier* override had set. Overriding twice therefore appended: unticking a
-- category in the dialog left it live on the message, and the queue showed both the old and the
-- new label at 100% confidence, each "set by reviewer". The dialog's own promise — "your choice
-- supersedes the AI's labels" — was not what the procedure did.
--
-- The fix records the highest classification id that exists before the inserts and supersedes
-- rows at or below it: the old rows, and only the old rows, whoever decided them.
--
-- Existing superseded_by chains are left exactly as they are. The record is append-only and a
-- migration must not rewrite decisions it did not witness; message 619 in the demo corpus keeps
-- the doubled labels its overrides actually produced.
--
-- The package body below is V6's, with only the change described above.

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
    v_cutoff NUMBER;
    v_first_new_id NUMBER;
  BEGIN
    -- Snapshot the live AI labels before anything changes, for the audit record.
    SELECT JSON_ARRAYAGG(category ORDER BY category RETURNING CLOB)
      INTO v_before
      FROM classification
     WHERE subject_type = 'MESSAGE'
       AND subject_id = p_message_id
       AND superseded_by IS NULL;

    IF p_decision = 'OVERRIDE' THEN
      -- Everything that exists *now* is what this override supersedes. Captured before the
      -- inserts so the new rows can be excluded by id rather than by decided_by (V7).
      SELECT NVL(MAX(id), 0) INTO v_cutoff FROM classification;

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
        v_first_new_id := NVL(v_first_new_id, v_new_id);
      END LOOP;

      -- Supersede every label that was live before this call, including labels an earlier
      -- override had set. The predicate used to be `decided_by <> 'REVIEWER'`, which spared the
      -- rows being inserted here but also spared every *previous* reviewer row: a second
      -- override appended its categories instead of replacing them, so unticking a category in
      -- the dialog left it on the message and the queue showed both. Excluding by id supersedes
      -- the old rows and only the old rows (V7).
      UPDATE classification
         SET superseded_by = v_first_new_id
       WHERE subject_type = 'MESSAGE'
         AND subject_id = p_message_id
         AND superseded_by IS NULL
         AND id <= v_cutoff;
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
