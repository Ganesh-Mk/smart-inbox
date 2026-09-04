package com.clinevo.inbox.queue;

import java.sql.CallableStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Java's side of {@code PKG_JOB_QUEUE}. All the queue logic lives in PL/SQL; this class only
 * calls it, so the durable behaviour is inspectable in SQL and testable without a JVM.
 */
@Repository
public class JobQueueRepository {

  private final JdbcTemplate jdbc;

  public JobQueueRepository(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  public long enqueue(JobType type, SubjectType subjectType, long subjectId) {
    return enqueue(type, subjectType, subjectId, 5, 0, null);
  }

  public long enqueue(
      JobType type,
      SubjectType subjectType,
      long subjectId,
      int priority,
      int delaySeconds,
      String payloadJson) {
    Long id =
        jdbc.execute(
            (org.springframework.jdbc.core.ConnectionCallback<Long>)
                con -> {
                  try (CallableStatement cs =
                      con.prepareCall(
                          "{? = call pkg_job_queue.enqueue(?, ?, ?, ?, ?, ?, ?)}")) {
                    cs.registerOutParameter(1, Types.NUMERIC);
                    cs.setString(2, type.name());
                    cs.setString(3, subjectType.name());
                    cs.setLong(4, subjectId);
                    cs.setInt(5, priority);
                    cs.setInt(6, delaySeconds);
                    cs.setInt(7, 3);
                    cs.setString(8, payloadJson);
                    cs.execute();
                    return cs.getLong(1);
                  }
                });
    return id == null ? -1L : id;
  }

  /**
   * Claims up to {@code limit} jobs for {@code workerId}.
   *
   * <p>Runs in its own short transaction — {@code REQUIRES_NEW} — and deliberately so. The
   * {@code FOR UPDATE SKIP LOCKED} row locks are held only until this commits; the claim is then
   * carried by {@code state = 'RUNNING'} instead. Holding database row locks for the minute a
   * handler spends waiting on the LLM would be a mistake: the lease and {@code reap_stale_locks}
   * are what protect an abandoned job (E37), not a long-lived lock.
   */
  @Transactional(propagation = Propagation.REQUIRES_NEW)
  public List<QueuedJob> dequeue(String workerId, int limit) {
    return jdbc.execute(
        (org.springframework.jdbc.core.ConnectionCallback<List<QueuedJob>>)
            con -> {
              try (CallableStatement cs =
                  con.prepareCall("{? = call pkg_job_queue.dequeue(?, ?)}")) {
                cs.registerOutParameter(1, Types.REF_CURSOR);
                cs.setString(2, workerId);
                cs.setInt(3, limit);
                cs.execute();
                try (ResultSet rs = (ResultSet) cs.getObject(1)) {
                  List<QueuedJob> jobs = new ArrayList<>();
                  while (rs.next()) {
                    jobs.add(map(rs));
                  }
                  return jobs;
                }
              }
            });
  }

  @Transactional(propagation = Propagation.REQUIRES_NEW)
  public void complete(long jobId) {
    jdbc.update("BEGIN pkg_job_queue.complete(?); END;", jobId);
  }

  /** Applies exponential backoff, or dead-letters the job if its attempts are exhausted (E38). */
  @Transactional(propagation = Propagation.REQUIRES_NEW)
  public void fail(long jobId, String error) {
    jdbc.update("BEGIN pkg_job_queue.fail(?, ?); END;", jobId, truncate(error));
  }

  /** Returns jobs abandoned by a crashed worker to PENDING; answers how many (E37). */
  @Transactional(propagation = Propagation.REQUIRES_NEW)
  public int reapStaleLocks(int leaseSeconds) {
    Integer recovered =
        jdbc.execute(
            (org.springframework.jdbc.core.ConnectionCallback<Integer>)
                con -> {
                  try (CallableStatement cs =
                      con.prepareCall("{? = call pkg_job_queue.reap_stale_locks(?)}")) {
                    cs.registerOutParameter(1, Types.NUMERIC);
                    cs.setInt(2, leaseSeconds);
                    cs.execute();
                    return cs.getInt(1);
                  }
                });
    return recovered == null ? 0 : recovered;
  }

  /** The E39 completion barrier: has every document of this message reached a terminal state? */
  public boolean allDocumentsTerminal(long messageId) {
    Integer result =
        jdbc.queryForObject(
            "SELECT pkg_job_queue.all_documents_terminal(?) FROM dual", Integer.class, messageId);
    return result != null && result == 1;
  }

  public int pendingCount(JobType type) {
    Integer count =
        jdbc.queryForObject(
            "SELECT pkg_job_queue.pending_count(?) FROM dual",
            Integer.class,
            type == null ? null : type.name());
    return count == null ? 0 : count;
  }

  public JobState stateOf(long jobId) {
    String state = jdbc.queryForObject("SELECT state FROM job WHERE id = ?", String.class, jobId);
    return state == null ? null : JobState.valueOf(state);
  }

  private static QueuedJob map(ResultSet rs) throws SQLException {
    return new QueuedJob(
        rs.getLong("id"),
        JobType.valueOf(rs.getString("job_type")),
        SubjectType.valueOf(rs.getString("subject_type")),
        rs.getLong("subject_id"),
        rs.getInt("attempts"),
        rs.getInt("max_attempts"),
        rs.getString("payload_json"));
  }

  private static String truncate(String error) {
    if (error == null) {
      return null;
    }
    return error.length() <= 4000 ? error : error.substring(0, 4000);
  }
}
