-- =====================================================================================
-- Smart Inbox — V3: PL/SQL packages
--
-- Oracle is not a data sink here. It holds the work queue and the audit logic, which is what
-- the brief's stated stack ("Oracle (PL/SQL)") actually implies (PROJECT_PLAN §5.1, §9.4).
--
--   PKG_JOB_QUEUE   durable queue: enqueue / dequeue (SKIP LOCKED) / complete / fail with
--                   exponential backoff / reap abandoned locks / the E39 completion barrier
--   PKG_AUDIT       append-only audit, written in an AUTONOMOUS_TRANSACTION so the record
--                   survives a rolled-back business transaction
--   PKG_REVIEW      reviewer override applied as a supersede, never an overwrite (E40)
-- =====================================================================================


-- =====================================================================================
-- PKG_JOB_QUEUE
-- =====================================================================================
CREATE OR REPLACE PACKAGE pkg_job_queue AS

  -- Lease after which a RUNNING job is presumed abandoned by a crashed worker (E37).
  c_default_lease_seconds CONSTANT NUMBER := 300;

  FUNCTION enqueue(
    p_type         IN VARCHAR2,
    p_subject_type IN VARCHAR2,
    p_subject_id   IN NUMBER,
    p_priority     IN NUMBER   DEFAULT 5,
    p_delay_s      IN NUMBER   DEFAULT 0,
    p_max_attempts IN NUMBER   DEFAULT 3,
    p_payload      IN VARCHAR2 DEFAULT NULL
  ) RETURN NUMBER;

  -- Claims up to p_limit jobs for p_worker and returns them. Uses FOR UPDATE SKIP LOCKED so
  -- concurrent workers never contend for, or double-claim, the same row.
  FUNCTION dequeue(
    p_worker IN VARCHAR2,
    p_limit  IN NUMBER DEFAULT 1
  ) RETURN SYS_REFCURSOR;

  PROCEDURE complete(p_job_id IN NUMBER);

  -- Exponential backoff; DEAD once attempts have run out, so a poison message cannot retry
  -- forever and burn the AI budget (E38).
  PROCEDURE fail(p_job_id IN NUMBER, p_error IN VARCHAR2);

  -- Returns abandoned RUNNING jobs to PENDING. Returns how many were recovered (E37).
  FUNCTION reap_stale_locks(p_lease_seconds IN NUMBER DEFAULT c_default_lease_seconds)
    RETURN NUMBER;

  -- The E39 completion barrier: 1 when every document of the message has reached a terminal
  -- parse state (PARSED or PARSE_FAILED), else 0. A failed document does not block the
  -- message; it is declared as missing in the prompt so the model can lower its confidence.
  FUNCTION all_documents_terminal(p_message_id IN NUMBER) RETURN NUMBER;

  FUNCTION pending_count(p_type IN VARCHAR2 DEFAULT NULL) RETURN NUMBER;

END pkg_job_queue;
/

CREATE OR REPLACE PACKAGE BODY pkg_job_queue AS

  FUNCTION enqueue(
    p_type         IN VARCHAR2,
    p_subject_type IN VARCHAR2,
    p_subject_id   IN NUMBER,
    p_priority     IN NUMBER   DEFAULT 5,
    p_delay_s      IN NUMBER   DEFAULT 0,
    p_max_attempts IN NUMBER   DEFAULT 3,
    p_payload      IN VARCHAR2 DEFAULT NULL
  ) RETURN NUMBER IS
    v_id NUMBER;
  BEGIN
    INSERT INTO job (job_type, subject_type, subject_id, state, priority,
                     max_attempts, available_at, payload_json)
    VALUES (p_type, p_subject_type, p_subject_id, 'PENDING', p_priority,
            p_max_attempts,
            SYSTIMESTAMP + NUMTODSINTERVAL(NVL(p_delay_s, 0), 'SECOND'),
            p_payload)
    RETURNING id INTO v_id;
    RETURN v_id;
  END enqueue;


  FUNCTION dequeue(
    p_worker IN VARCHAR2,
    p_limit  IN NUMBER DEFAULT 1
  ) RETURN SYS_REFCURSOR IS
    -- SKIP LOCKED is the whole point: a row another worker already holds is stepped over
    -- rather than waited on, so N workers make progress in parallel with no double-dequeue.
    CURSOR c_claimable IS
      SELECT id
        FROM job
       WHERE state = 'PENDING'
         AND available_at <= SYSTIMESTAMP
       ORDER BY priority ASC, id ASC
         FOR UPDATE SKIP LOCKED;

    -- SYS.ODCINUMBERLIST rather than an associative array: it is a SQL type, so the claimed
    -- ids can be bound straight into the TABLE() below and the result cursor selects exactly
    -- the rows this call won — no "locked_by = me" heuristic that a previous call could spoil.
    v_ids    SYS.ODCINUMBERLIST;
    v_result SYS_REFCURSOR;
    v_count  PLS_INTEGER;
  BEGIN
    OPEN c_claimable;
    FETCH c_claimable BULK COLLECT INTO v_ids LIMIT GREATEST(NVL(p_limit, 1), 1);
    CLOSE c_claimable;

    v_count := v_ids.COUNT;

    IF v_count > 0 THEN
      FORALL i IN 1 .. v_count
        UPDATE job
           SET state      = 'RUNNING',
               locked_by  = p_worker,
               locked_at  = SYSTIMESTAMP,
               attempts   = attempts + 1,
               updated_at = SYSTIMESTAMP
         WHERE id = v_ids(i);
    END IF;

    -- Always return a cursor, empty when nothing was claimed, so the Java worker loop has
    -- exactly one code path.
    OPEN v_result FOR
      SELECT j.id, j.job_type, j.subject_type, j.subject_id, j.state, j.priority,
             j.attempts, j.max_attempts, j.payload_json
        FROM job j
       WHERE j.id IN (SELECT column_value FROM TABLE(v_ids))
       ORDER BY j.priority, j.id;

    RETURN v_result;
  END dequeue;


  PROCEDURE complete(p_job_id IN NUMBER) IS
  BEGIN
    UPDATE job
       SET state      = 'DONE',
           locked_by  = NULL,
           locked_at  = NULL,
           last_error = NULL,
           updated_at = SYSTIMESTAMP
     WHERE id = p_job_id;
  END complete;


  PROCEDURE fail(p_job_id IN NUMBER, p_error IN VARCHAR2) IS
    v_attempts     NUMBER;
    v_max_attempts NUMBER;
  BEGIN
    SELECT attempts, max_attempts
      INTO v_attempts, v_max_attempts
      FROM job
     WHERE id = p_job_id
       FOR UPDATE;

    IF v_attempts >= v_max_attempts THEN
      -- E38: out of attempts. DEAD is terminal and is never retried automatically; the item
      -- stays visible in the UI so a human can decide what to do with it.
      UPDATE job
         SET state      = 'DEAD',
             locked_by  = NULL,
             locked_at  = NULL,
             last_error = SUBSTR(p_error, 1, 4000),
             updated_at = SYSTIMESTAMP
       WHERE id = p_job_id;
    ELSE
      -- Exponential backoff: 2^attempts seconds before the job becomes claimable again.
      UPDATE job
         SET state        = 'PENDING',
             locked_by    = NULL,
             locked_at    = NULL,
             last_error   = SUBSTR(p_error, 1, 4000),
             available_at = SYSTIMESTAMP + NUMTODSINTERVAL(POWER(2, v_attempts), 'SECOND'),
             updated_at   = SYSTIMESTAMP
       WHERE id = p_job_id;
    END IF;
  END fail;


  FUNCTION reap_stale_locks(p_lease_seconds IN NUMBER DEFAULT c_default_lease_seconds)
    RETURN NUMBER IS
    v_recovered NUMBER;
  BEGIN
    -- A worker that crashed mid-job leaves its row RUNNING forever. Anything locked past its
    -- lease goes back to PENDING; every handler is idempotent, so re-running is always safe.
    UPDATE job
       SET state      = 'PENDING',
           locked_by  = NULL,
           locked_at  = NULL,
           last_error = 'Recovered by reap_stale_locks after lease expiry',
           updated_at = SYSTIMESTAMP
     WHERE state = 'RUNNING'
       AND locked_at IS NOT NULL
       AND locked_at < SYSTIMESTAMP - NUMTODSINTERVAL(p_lease_seconds, 'SECOND');

    v_recovered := SQL%ROWCOUNT;
    RETURN v_recovered;
  END reap_stale_locks;


  FUNCTION all_documents_terminal(p_message_id IN NUMBER) RETURN NUMBER IS
    v_outstanding NUMBER;
    v_total       NUMBER;
  BEGIN
    SELECT COUNT(*),
           COUNT(CASE WHEN parse_status NOT IN ('PARSED', 'PARSE_FAILED') THEN 1 END)
      INTO v_total, v_outstanding
      FROM document
     WHERE message_id = p_message_id;

    -- A message with no documents at all is not "complete" — ingestion always creates at
    -- least the EMAIL_BODY document (E11), so zero means ingestion has not finished.
    IF v_total = 0 THEN
      RETURN 0;
    END IF;

    RETURN CASE WHEN v_outstanding = 0 THEN 1 ELSE 0 END;
  END all_documents_terminal;


  FUNCTION pending_count(p_type IN VARCHAR2 DEFAULT NULL) RETURN NUMBER IS
    v_count NUMBER;
  BEGIN
    SELECT COUNT(*)
      INTO v_count
      FROM job
     WHERE state = 'PENDING'
       AND (p_type IS NULL OR job_type = p_type);
    RETURN v_count;
  END pending_count;

END pkg_job_queue;
/


-- =====================================================================================
-- PKG_AUDIT
--
-- PRAGMA AUTONOMOUS_TRANSACTION is the reason this lives in PL/SQL rather than Java: the
-- audit row commits independently, so it survives even when the business transaction that
-- triggered it rolls back. "We tried to do X and it failed" is exactly the event a regulated
-- system must not lose.
-- =====================================================================================
CREATE OR REPLACE PACKAGE pkg_audit AS

  PROCEDURE log(
    p_actor       IN VARCHAR2,
    p_actor_type  IN VARCHAR2,
    p_action      IN VARCHAR2,
    p_entity_type IN VARCHAR2,
    p_entity_id   IN NUMBER,
    p_before      IN CLOB     DEFAULT NULL,
    p_after       IN CLOB     DEFAULT NULL,
    p_corr        IN VARCHAR2 DEFAULT NULL,
    p_message_id  IN NUMBER   DEFAULT NULL
  );

END pkg_audit;
/

CREATE OR REPLACE PACKAGE BODY pkg_audit AS

  PROCEDURE log(
    p_actor       IN VARCHAR2,
    p_actor_type  IN VARCHAR2,
    p_action      IN VARCHAR2,
    p_entity_type IN VARCHAR2,
    p_entity_id   IN NUMBER,
    p_before      IN CLOB     DEFAULT NULL,
    p_after       IN CLOB     DEFAULT NULL,
    p_corr        IN VARCHAR2 DEFAULT NULL,
    p_message_id  IN NUMBER   DEFAULT NULL
  ) IS
    PRAGMA AUTONOMOUS_TRANSACTION;
  BEGIN
    INSERT INTO audit_event (correlation_id, actor, actor_type, action,
                             entity_type, entity_id, message_id, before_json, after_json)
    VALUES (p_corr, NVL(p_actor, 'system'), NVL(p_actor_type, 'SYSTEM'), p_action,
            p_entity_type, p_entity_id, p_message_id, p_before, p_after);
    COMMIT;
  EXCEPTION
    WHEN OTHERS THEN
      -- Auditing must never take the application down, but a silent failure would be worse
      -- than useless. Roll back the autonomous transaction and re-raise.
      ROLLBACK;
      RAISE;
  END log;

END pkg_audit;
/


-- =====================================================================================
-- PKG_REVIEW
--
-- E40: the reviewer's decision is applied as a supersede. The AI's rows are left exactly as
-- they were and pointed at their replacement, so the original machine answer is recoverable
-- forever and the diff between machine and human is a query, not an archaeology exercise.
-- =====================================================================================
CREATE OR REPLACE PACKAGE pkg_review AS

  PROCEDURE apply_override(
    p_message_id IN NUMBER,
    p_reviewer   IN VARCHAR2,
    p_categories IN VARCHAR2,          -- JSON array, e.g. ["ICSR","PQC"]
    p_decision   IN VARCHAR2,          -- ACCEPT | OVERRIDE | REJECT
    p_notes      IN VARCHAR2 DEFAULT NULL,
    p_corr       IN VARCHAR2 DEFAULT NULL
  );

  PROCEDURE override_field(
    p_field_id   IN NUMBER,
    p_reviewer   IN VARCHAR2,
    p_value      IN VARCHAR2,
    p_status     IN VARCHAR2,
    p_note       IN VARCHAR2 DEFAULT NULL,
    p_corr       IN VARCHAR2 DEFAULT NULL,
    p_new_id     OUT NUMBER
  );

END pkg_review;
/

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

    UPDATE inbox_message
       SET status = 'REVIEWED',
           updated_at = SYSTIMESTAMP
     WHERE id = p_message_id;

    pkg_audit.log(
      p_actor       => p_reviewer,
      p_actor_type  => 'REVIEWER',
      p_action      => 'REVIEW_' || p_decision,
      p_entity_type => 'MESSAGE',
      p_entity_id   => p_message_id,
      p_before      => v_before,
      p_after       => TO_CLOB(p_categories),
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
