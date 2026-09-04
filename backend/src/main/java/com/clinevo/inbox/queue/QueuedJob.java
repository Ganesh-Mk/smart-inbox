package com.clinevo.inbox.queue;

/**
 * One claimed unit of work, exactly as {@code PKG_JOB_QUEUE.dequeue} returned it.
 *
 * <p>{@code attempts} has already been incremented by the dequeue, so it is the number of the
 * attempt now in progress — which is what the backoff in {@code PKG_JOB_QUEUE.fail} is computed
 * from.
 */
public record QueuedJob(
    long id,
    JobType type,
    SubjectType subjectType,
    long subjectId,
    int attempts,
    int maxAttempts,
    String payloadJson) {

  /** True when a failure now would exhaust the retry budget and dead-letter the job (E38). */
  public boolean isLastAttempt() {
    return attempts >= maxAttempts;
  }
}
