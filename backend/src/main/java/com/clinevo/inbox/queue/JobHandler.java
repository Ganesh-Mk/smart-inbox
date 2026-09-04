package com.clinevo.inbox.queue;

/**
 * Service-provider interface for one stage of the pipeline.
 *
 * <p><strong>Every implementation must be idempotent.</strong> A worker can die mid-job and
 * {@code reap_stale_locks} will hand the same job to someone else (E37); a transport failure
 * retries with backoff (E36). Handlers therefore delete-then-insert keyed on
 * {@code (subject, stage)} rather than blindly appending, so re-running is always safe.
 */
public interface JobHandler {

  /** The job type this handler is registered for. */
  JobType type();

  /**
   * Does the work. Throwing marks the job failed and applies backoff; returning normally
   * completes it.
   *
   * @throws Exception any failure — the message is recorded in {@code JOB.last_error}
   */
  void handle(QueuedJob job) throws Exception;
}
