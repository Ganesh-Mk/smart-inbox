package com.clinevo.inbox.queue;

import static org.assertj.core.api.Assertions.assertThat;

import com.clinevo.inbox.OracleIntegrationTest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * The claim that {@code FOR UPDATE SKIP LOCKED} makes the queue safe under concurrency is worth
 * exactly as much as the test behind it. This is that test.
 */
@OracleIntegrationTest
class JobQueueConcurrencyTest {

  @Autowired JobQueueRepository queue;
  @Autowired JdbcTemplate jdbc;

  private static final long TEST_SUBJECT_BASE = 900_000L;

  @BeforeEach
  void clearTestJobs() {
    jdbc.update("DELETE FROM job WHERE subject_id >= ?", TEST_SUBJECT_BASE);
  }

  @Test
  @DisplayName("eight threads racing for twenty jobs claim each job exactly once")
  void skipLockedGivesNoDoubleDequeue() throws Exception {
    int jobCount = 20;
    int workers = 8;
    for (int i = 0; i < jobCount; i++) {
      queue.enqueue(JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + i);
    }

    // A barrier so every thread hits dequeue at genuinely the same moment; staggered starts
    // would let each worker finish before the next began and prove nothing.
    CyclicBarrier startLine = new CyclicBarrier(workers);
    ExecutorService pool = Executors.newFixedThreadPool(workers);
    List<Callable<List<Long>>> tasks = new ArrayList<>();
    for (int w = 0; w < workers; w++) {
      String workerId = "test-worker-" + w;
      tasks.add(() -> {
        startLine.await(20, TimeUnit.SECONDS);
        List<Long> claimed = new ArrayList<>();
        // Each worker drains until the queue is empty, so between them they take all 20.
        for (int round = 0; round < 20; round++) {
          List<QueuedJob> batch = queue.dequeue(workerId, 3);
          if (batch.isEmpty()) {
            break;
          }
          batch.forEach(j -> claimed.add(j.id()));
        }
        return claimed;
      });
    }

    List<Future<List<Long>>> futures = pool.invokeAll(tasks, 60, TimeUnit.SECONDS);
    pool.shutdown();

    List<Long> allClaims = new ArrayList<>();
    for (Future<List<Long>> f : futures) {
      allClaims.addAll(f.get());
    }

    Set<Long> distinct = new HashSet<>(allClaims);
    assertThat(allClaims)
        .as("no job may be handed to two workers")
        .hasSameSizeAs(distinct);
    assertThat(distinct).as("every enqueued job is claimed exactly once").hasSize(jobCount);

    Integer stillPending = jdbc.queryForObject(
        "SELECT COUNT(*) FROM job WHERE subject_id >= ? AND state = 'PENDING'",
        Integer.class, TEST_SUBJECT_BASE);
    assertThat(stillPending).isZero();
  }

  @Test
  @DisplayName("dequeue marks jobs RUNNING, stamps the worker and increments attempts")
  void dequeueClaimsAndStamps() {
    long jobId = queue.enqueue(JobType.CLASSIFY_MESSAGE, SubjectType.MESSAGE, TEST_SUBJECT_BASE + 50);

    List<QueuedJob> claimed = queue.dequeue("worker-A", 5);

    assertThat(claimed).extracting(QueuedJob::id).contains(jobId);
    QueuedJob job = claimed.stream().filter(j -> j.id() == jobId).findFirst().orElseThrow();
    assertThat(job.type()).isEqualTo(JobType.CLASSIFY_MESSAGE);
    assertThat(job.attempts()).isEqualTo(1);
    assertThat(queue.stateOf(jobId)).isEqualTo(JobState.RUNNING);

    String lockedBy = jdbc.queryForObject(
        "SELECT locked_by FROM job WHERE id = ?", String.class, jobId);
    assertThat(lockedBy).isEqualTo("worker-A");
  }

  @Test
  @DisplayName("a delayed job is invisible until its available_at passes")
  void delayedJobIsNotClaimableYet() {
    long delayed = queue.enqueue(
        JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + 60, 5, 3600, null);

    List<QueuedJob> claimed = queue.dequeue("worker-B", 10);

    assertThat(claimed).extracting(QueuedJob::id).doesNotContain(delayed);
    assertThat(queue.stateOf(delayed)).isEqualTo(JobState.PENDING);
  }

  @Test
  @DisplayName("lower priority number is served first")
  void priorityOrdersTheQueue() {
    long low = queue.enqueue(
        JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + 70, 9, 0, null);
    long high = queue.enqueue(
        JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, TEST_SUBJECT_BASE + 71, 1, 0, null);

    List<QueuedJob> claimed = queue.dequeue("worker-C", 1);

    assertThat(claimed).hasSize(1);
    assertThat(claimed.get(0).id()).isEqualTo(high);
    assertThat(queue.stateOf(low)).isEqualTo(JobState.PENDING);
  }
}
