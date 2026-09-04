package com.clinevo.inbox.queue;

import static org.assertj.core.api.Assertions.assertThat;

import com.clinevo.inbox.OracleIntegrationTest;
import java.sql.Timestamp;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

/** Failure handling: backoff (E36), dead-lettering (E38) and lock recovery (E37). */
@OracleIntegrationTest
class JobQueueRetryTest {

  @Autowired JobQueueRepository queue;
  @Autowired JdbcTemplate jdbc;

  private static final long TEST_SUBJECT_BASE = 910_000L;

  @BeforeEach
  void clearTestJobs() {
    jdbc.update("DELETE FROM job WHERE subject_id >= ? AND subject_id < ?",
        TEST_SUBJECT_BASE, TEST_SUBJECT_BASE + 1000);
  }

  @Test
  @DisplayName("a failure returns the job to PENDING with an exponential backoff delay")
  void failureAppliesExponentialBackoff() {
    long jobId = queue.enqueue(JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + 1);
    queue.dequeue("worker-A", 1);

    queue.fail(jobId, "AI service returned 503");

    assertThat(queue.stateOf(jobId)).isEqualTo(JobState.PENDING);
    assertThat(errorOf(jobId)).contains("503");

    // attempts == 1 after the first dequeue, so the delay is 2^1 = 2 seconds.
    long delaySeconds = secondsUntilAvailable(jobId);
    assertThat(delaySeconds).as("2^1 seconds of backoff").isBetween(1L, 3L);

    // Second failure: attempts == 2, so 2^2 = 4 seconds.
    jdbc.update("UPDATE job SET available_at = SYSTIMESTAMP WHERE id = ?", jobId);
    queue.dequeue("worker-A", 1);
    queue.fail(jobId, "AI service returned 503 again");
    assertThat(secondsUntilAvailable(jobId)).as("2^2 seconds of backoff").isBetween(3L, 5L);

    // The lock is released on failure so another worker can pick it up after the delay.
    assertThat(lockedByOf(jobId)).isNull();
  }

  @Test
  @DisplayName("a poison job is dead-lettered once its attempts are exhausted and never retried")
  void poisonJobBecomesDead() {
    long jobId = queue.enqueue(JobType.EXTRACT_CASE, SubjectType.MESSAGE, TEST_SUBJECT_BASE + 2);

    // max_attempts defaults to 3: fail it three times.
    for (int attempt = 1; attempt <= 3; attempt++) {
      jdbc.update("UPDATE job SET available_at = SYSTIMESTAMP WHERE id = ?", jobId);
      List<QueuedJob> claimed = queue.dequeue("worker-A", 1);
      assertThat(claimed).as("attempt %d should be claimable", attempt).hasSize(1);
      assertThat(claimed.get(0).attempts()).isEqualTo(attempt);
      queue.fail(jobId, "deterministic failure " + attempt);
    }

    assertThat(queue.stateOf(jobId)).isEqualTo(JobState.DEAD);

    // E38: DEAD is terminal. It must never be handed out again, even with time passed.
    jdbc.update("UPDATE job SET available_at = SYSTIMESTAMP - INTERVAL '1' HOUR WHERE id = ?", jobId);
    assertThat(queue.dequeue("worker-A", 10))
        .as("a DEAD job is never retried automatically")
        .extracting(QueuedJob::id)
        .doesNotContain(jobId);
  }

  @Test
  @DisplayName("reap_stale_locks recovers a job abandoned by a crashed worker")
  void reapStaleLocksRecoversAbandonedJobs() {
    long abandoned = queue.enqueue(JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + 3);
    long healthy = queue.enqueue(JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + 4);
    queue.dequeue("worker-that-will-crash", 2);

    // Simulate the crash: the row stays RUNNING with a lock that is now older than the lease.
    jdbc.update("UPDATE job SET locked_at = SYSTIMESTAMP - INTERVAL '600' SECOND WHERE id = ?",
        abandoned);

    int recovered = queue.reapStaleLocks(300);

    assertThat(recovered).isEqualTo(1);
    assertThat(queue.stateOf(abandoned)).isEqualTo(JobState.PENDING);
    assertThat(lockedByOf(abandoned)).isNull();
    assertThat(queue.stateOf(healthy))
        .as("a job still within its lease is left alone")
        .isEqualTo(JobState.RUNNING);

    assertThat(queue.dequeue("fresh-worker", 5))
        .extracting(QueuedJob::id)
        .contains(abandoned);
  }

  @Test
  @DisplayName("complete() clears the lock and the error")
  void completeClearsLockAndError() {
    long jobId = queue.enqueue(JobType.FINALISE_MESSAGE, SubjectType.MESSAGE, TEST_SUBJECT_BASE + 5);
    queue.dequeue("worker-A", 1);
    queue.fail(jobId, "transient blip");
    jdbc.update("UPDATE job SET available_at = SYSTIMESTAMP WHERE id = ?", jobId);
    queue.dequeue("worker-A", 1);

    queue.complete(jobId);

    assertThat(queue.stateOf(jobId)).isEqualTo(JobState.DONE);
    assertThat(lockedByOf(jobId)).isNull();
    assertThat(errorOf(jobId)).isNull();
  }

  private long secondsUntilAvailable(long jobId) {
    Timestamp availableAt = jdbc.queryForObject(
        "SELECT CAST(available_at AS TIMESTAMP) FROM job WHERE id = ?", Timestamp.class, jobId);
    Timestamp now = jdbc.queryForObject(
        "SELECT CAST(SYSTIMESTAMP AS TIMESTAMP) FROM dual", Timestamp.class);
    return Math.round((availableAt.getTime() - now.getTime()) / 1000.0);
  }

  private String lockedByOf(long jobId) {
    return jdbc.queryForObject("SELECT locked_by FROM job WHERE id = ?", String.class, jobId);
  }

  private String errorOf(long jobId) {
    return jdbc.queryForObject("SELECT last_error FROM job WHERE id = ?", String.class, jobId);
  }
}
