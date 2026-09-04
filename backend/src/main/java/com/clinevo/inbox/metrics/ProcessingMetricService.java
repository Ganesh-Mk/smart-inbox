package com.clinevo.inbox.metrics;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Records per-stage timings into {@code PROCESSING_METRIC}.
 *
 * <p>This is the data behind deliverable R17 — "process a batch of 10–15 documents and report how
 * long each one takes". Recorded for failures as well as successes, because a stage that reliably
 * takes 40 seconds before dying is exactly the thing a timing report should show.
 */
@Service
public class ProcessingMetricService {

  private static final Logger log = LoggerFactory.getLogger(ProcessingMetricService.class);

  private final JdbcTemplate jdbc;

  public ProcessingMetricService(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  /**
   * Writes one metric row in its own transaction, so a metric is never lost to the rollback of
   * the business transaction it was measuring.
   */
  @Transactional(propagation = Propagation.REQUIRES_NEW)
  public void record(String subjectType, long subjectId, String stage, long durationMs, boolean ok) {
    try {
      jdbc.update(
          "INSERT INTO processing_metric (subject_type, subject_id, stage, duration_ms, succeeded)"
              + " VALUES (?, ?, ?, ?, ?)",
          subjectType,
          subjectId,
          stage,
          durationMs,
          ok ? "Y" : "N");
    } catch (RuntimeException e) {
      // Telemetry must never be the reason a job fails.
      log.warn("Could not record metric {}/{} for {} {}", stage, durationMs, subjectType, subjectId, e);
    }
  }
}
