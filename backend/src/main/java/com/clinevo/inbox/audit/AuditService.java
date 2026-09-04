package com.clinevo.inbox.audit;

import java.sql.CallableStatement;
import java.sql.Types;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * Writes the audit trail through {@code PKG_AUDIT.log}.
 *
 * <p>The procedure is declared {@code PRAGMA AUTONOMOUS_TRANSACTION}, so the audit row commits on
 * its own connection and survives a rollback of the business transaction that triggered it. That
 * is the whole reason it is PL/SQL rather than another JDBC insert here: "we attempted X and it
 * failed" is precisely the event a regulated system must not lose.
 */
@Service
public class AuditService {

  private static final Logger log = LoggerFactory.getLogger(AuditService.class);

  public static final String SYSTEM_ACTOR = "system";

  private final JdbcTemplate jdbc;

  public AuditService(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  public void system(String action, String entityType, Long entityId, String afterJson) {
    log(SYSTEM_ACTOR, "SYSTEM", action, entityType, entityId, null, afterJson, null, null);
  }

  public void systemForMessage(String action, String entityType, Long entityId, String afterJson,
      Long messageId) {
    log(SYSTEM_ACTOR, "SYSTEM", action, entityType, entityId, null, afterJson, null, messageId);
  }

  public void reviewer(String reviewer, String action, String entityType, Long entityId,
      String beforeJson, String afterJson, Long messageId) {
    log(reviewer, "REVIEWER", action, entityType, entityId, beforeJson, afterJson, null, messageId);
  }

  public void log(
      String actor,
      String actorType,
      String action,
      String entityType,
      Long entityId,
      String beforeJson,
      String afterJson,
      String correlationId,
      Long messageId) {
    try {
      jdbc.execute(
          (ConnectionCallback<Void>)
              con -> {
                try (CallableStatement cs =
                    con.prepareCall(
                        "BEGIN pkg_audit.log(?, ?, ?, ?, ?, ?, ?, ?, ?); END;")) {
                  cs.setString(1, actor);
                  cs.setString(2, actorType);
                  cs.setString(3, action);
                  cs.setString(4, entityType);
                  setNullableLong(cs, 5, entityId);
                  setNullableClob(cs, 6, beforeJson);
                  setNullableClob(cs, 7, afterJson);
                  cs.setString(8, correlationId);
                  setNullableLong(cs, 9, messageId);
                  cs.execute();
                  return null;
                }
              });
    } catch (RuntimeException e) {
      // Deliberately loud. An audit write that fails is a serious problem, but taking the
      // pipeline down with it would turn one lost record into a lost batch.
      log.error("AUDIT WRITE FAILED: {} {} {} by {}", action, entityType, entityId, actor, e);
    }
  }

  private static void setNullableLong(CallableStatement cs, int index, Long value)
      throws java.sql.SQLException {
    if (value == null) {
      cs.setNull(index, Types.NUMERIC);
    } else {
      cs.setLong(index, value);
    }
  }

  private static void setNullableClob(CallableStatement cs, int index, String value)
      throws java.sql.SQLException {
    if (value == null) {
      cs.setNull(index, Types.CLOB);
    } else {
      cs.setString(index, value);
    }
  }
}
