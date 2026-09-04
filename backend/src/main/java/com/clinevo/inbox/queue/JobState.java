package com.clinevo.inbox.queue;

/** Job lifecycle. {@code DEAD} is terminal and is never retried automatically (E38). */
public enum JobState {
  PENDING,
  RUNNING,
  DONE,
  FAILED,
  DEAD
}
