package com.clinevo.inbox.audit;

import static org.assertj.core.api.Assertions.assertThat;

import com.clinevo.inbox.OracleIntegrationTest;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The audit trail's single most important property: an audit record survives the rollback of the
 * business transaction that wrote it.
 *
 * <p>Without {@code PRAGMA AUTONOMOUS_TRANSACTION} these tests fail, and a regulated system would
 * silently lose exactly the events it most needs — the attempts that went wrong.
 */
@OracleIntegrationTest
class AuditAutonomousTransactionTest {

  @Autowired AuditService audit;
  @Autowired JdbcTemplate jdbc;
  @Autowired TransactionTemplate txTemplate;

  @Test
  @DisplayName("an audit row written inside a rolled-back transaction still commits")
  void auditSurvivesRollback() {
    String correlationId = UUID.randomUUID().toString();

    // A business transaction that writes a row, audits the attempt, and then blows up.
    try {
      txTemplate.executeWithoutResult(status -> {
        jdbc.update(
            "INSERT INTO inbox_message (dedupe_key, subject, status) VALUES (?, ?, 'RECEIVED')",
            correlationId, "rollback probe");

        audit.log("system", "SYSTEM", "PROBE_ATTEMPT", "INBOX_MESSAGE", null,
            null, "{\"probe\":true}", correlationId, null);

        throw new IllegalStateException("business failure after the audit write");
      });
    } catch (IllegalStateException expected) {
      // the rollback is the point of the test
    }

    Integer businessRows = jdbc.queryForObject(
        "SELECT COUNT(*) FROM inbox_message WHERE dedupe_key = ?", Integer.class, correlationId);
    assertThat(businessRows).as("the business write must have rolled back").isZero();

    Integer auditRows = jdbc.queryForObject(
        "SELECT COUNT(*) FROM audit_event WHERE correlation_id = ?", Integer.class, correlationId);
    assertThat(auditRows)
        .as("the audit record must survive the rollback — this is why PKG_AUDIT is autonomous")
        .isEqualTo(1);

    String action = jdbc.queryForObject(
        "SELECT action FROM audit_event WHERE correlation_id = ?", String.class, correlationId);
    assertThat(action).isEqualTo("PROBE_ATTEMPT");

    jdbc.update("DELETE FROM audit_event WHERE correlation_id = ?", correlationId);
  }

  @Test
  @DisplayName("before/after JSON and the actor are recorded verbatim")
  void auditRecordsActorAndPayloads() {
    String correlationId = UUID.randomUUID().toString();

    audit.log("dr.reviewer", "REVIEWER", "FIELD_OVERRIDE", "EXTRACTED_FIELD", 4242L,
        "{\"value\":\"58\"}", "{\"value\":\"85\"}", correlationId, null);

    var row = jdbc.queryForMap(
        "SELECT actor, actor_type, action, entity_id,"
            + " TO_CHAR(before_json) AS before_json, TO_CHAR(after_json) AS after_json"
            + " FROM audit_event WHERE correlation_id = ?", correlationId);

    assertThat(row.get("ACTOR")).isEqualTo("dr.reviewer");
    assertThat(row.get("ACTOR_TYPE")).isEqualTo("REVIEWER");
    assertThat(row.get("ACTION")).isEqualTo("FIELD_OVERRIDE");
    assertThat(((Number) row.get("ENTITY_ID")).longValue()).isEqualTo(4242L);
    assertThat((String) row.get("BEFORE_JSON")).contains("58");
    assertThat((String) row.get("AFTER_JSON")).contains("85");

    jdbc.update("DELETE FROM audit_event WHERE correlation_id = ?", correlationId);
  }
}
