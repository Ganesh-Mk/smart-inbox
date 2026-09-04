package com.clinevo.inbox.queue;

/**
 * The pipeline, as job types. Sequencing (PROJECT_PLAN §5.2):
 *
 * <pre>
 * PARSE_DOCUMENT   (fan-out: one per document)
 *   └── completion barrier — every document terminal (E39)
 *         └── CLASSIFY_MESSAGE
 *               └── EXTRACT_CASE   (fan-out: one per matched category)
 *                     └── FINALISE_MESSAGE   (merge, conflict detect, verify, ready for review)
 *
 * SCREEN_ARTICLE     bonus path, entered directly from the upload endpoint
 * </pre>
 */
public enum JobType {
  PARSE_DOCUMENT,
  CLASSIFY_MESSAGE,
  EXTRACT_CASE,
  FINALISE_MESSAGE,
  SCREEN_ARTICLE
}
