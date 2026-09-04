package com.clinevo.inbox.queue;

import com.clinevo.inbox.config.AppProperties;
import com.clinevo.inbox.metrics.ProcessingMetricService;
import jakarta.annotation.PreDestroy;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * N daemon threads, each looping: claim work, run the handler, report the outcome.
 *
 * <p>Nothing on the request path is synchronous — ingestion enqueues and returns, and these
 * workers do the AI work in the background (R18). The pool is deliberately thin: all the
 * durability lives in {@code PKG_JOB_QUEUE}, so a JVM restart loses nothing.
 */
@Component
public class JobWorkerPool {

  private static final Logger log = LoggerFactory.getLogger(JobWorkerPool.class);

  private final JobQueueRepository queue;
  private final AppProperties props;
  private final ProcessingMetricService metrics;
  private final Map<JobType, JobHandler> handlers = new EnumMap<>(JobType.class);

  private final AtomicBoolean running = new AtomicBoolean(false);
  private ExecutorService executor;

  public JobWorkerPool(
      JobQueueRepository queue,
      AppProperties props,
      ProcessingMetricService metrics,
      List<JobHandler> handlerBeans) {
    this.queue = queue;
    this.props = props;
    this.metrics = metrics;
    for (JobHandler handler : handlerBeans) {
      JobHandler previous = handlers.put(handler.type(), handler);
      if (previous != null) {
        throw new IllegalStateException(
            "Two handlers registered for " + handler.type() + ": "
                + previous.getClass().getName() + " and " + handler.getClass().getName());
      }
    }
  }

  @EventListener(ApplicationReadyEvent.class)
  public void start() {
    if (!running.compareAndSet(false, true)) {
      return;
    }
    int threads = props.queue().workerThreads();
    if (threads <= 0) {
      // Tests drive the queue directly; a background pool would steal their jobs.
      log.info("Job worker pool disabled (inbox.queue.worker-threads = {})", threads);
      return;
    }
    executor = Executors.newFixedThreadPool(threads, r -> {
      Thread t = new Thread(r);
      t.setName("job-worker-" + t.threadId());
      t.setDaemon(true);
      return t;
    });
    for (int i = 0; i < threads; i++) {
      executor.submit(this::workerLoop);
    }
    log.info("Job worker pool started with {} threads, handlers for {}", threads, handlers.keySet());
  }

  @PreDestroy
  public void stop() {
    if (!running.compareAndSet(true, false)) {
      return;
    }
    if (executor != null) {
      executor.shutdownNow();
      try {
        if (!executor.awaitTermination(10, TimeUnit.SECONDS)) {
          log.warn("Worker pool did not stop within 10s; jobs left RUNNING will be reaped");
        }
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    }
    log.info("Job worker pool stopped");
  }

  private void workerLoop() {
    String workerId = workerId();
    log.debug("Worker {} started", workerId);
    while (running.get() && !Thread.currentThread().isInterrupted()) {
      try {
        List<QueuedJob> claimed = queue.dequeue(workerId, props.queue().batchSize());
        if (claimed.isEmpty()) {
          Thread.sleep(props.queue().pollIntervalMs());
          continue;
        }
        for (QueuedJob job : claimed) {
          runOne(job);
        }
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
        return;
      } catch (RuntimeException e) {
        // A failure to talk to the database at all: back off rather than spin hot.
        log.error("Worker {} loop error", workerId, e);
        sleepQuietly(props.queue().pollIntervalMs() * 5);
      }
    }
    log.debug("Worker {} stopped", workerId);
  }

  /** Runs one job. Never throws: the outcome is always reported back to the queue. */
  void runOne(QueuedJob job) {
    JobHandler handler = handlers.get(job.type());
    if (handler == null) {
      log.error("No handler registered for {} (job {})", job.type(), job.id());
      queue.fail(job.id(), "No handler registered for " + job.type());
      return;
    }

    long started = System.nanoTime();
    boolean ok = false;
    try {
      handler.handle(job);
      queue.complete(job.id());
      ok = true;
      log.debug("Job {} ({}) done", job.id(), job.type());
    } catch (Exception e) {
      String message = e.getClass().getSimpleName() + ": " + e.getMessage();
      // On the final attempt this dead-letters the job rather than retrying forever (E38).
      queue.fail(job.id(), message);
      if (job.isLastAttempt()) {
        log.error("Job {} ({}) dead-lettered after {} attempts: {}",
            job.id(), job.type(), job.attempts(), message, e);
      } else {
        log.warn("Job {} ({}) failed on attempt {}, will retry: {}",
            job.id(), job.type(), job.attempts(), message);
      }
    } finally {
      long elapsedMs = (System.nanoTime() - started) / 1_000_000;
      metrics.record(job.subjectType().name(), job.subjectId(), job.type().name(), elapsedMs, ok);
    }
  }

  /**
   * Returns jobs abandoned by a crashed worker to PENDING (E37). Every handler is idempotent, so
   * re-running a job that may have partly completed is always safe.
   */
  @Scheduled(fixedDelayString = "${inbox.queue.reap-interval-ms:60000}")
  public void reapStaleLocks() {
    try {
      int recovered = queue.reapStaleLocks(props.queue().leaseSeconds());
      if (recovered > 0) {
        log.warn("Recovered {} job(s) abandoned past their {}s lease",
            recovered, props.queue().leaseSeconds());
      }
    } catch (RuntimeException e) {
      log.error("reap_stale_locks failed", e);
    }
  }

  private static String workerId() {
    return Thread.currentThread().getName() + "@" + ProcessHandle.current().pid();
  }

  private static void sleepQuietly(long ms) {
    try {
      Thread.sleep(ms);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    }
  }
}
