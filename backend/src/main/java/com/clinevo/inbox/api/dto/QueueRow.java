package com.clinevo.inbox.api.dto;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * One row of the reviewer's work queue, straight from {@code V_REVIEW_QUEUE}.
 *
 * <p>The flags are the point. A reviewer does not want a list of messages, they want to know
 * which messages need them: where the model is unsure, where it cited something that could not
 * be found, where two sources disagree, and where a document failed to parse.
 */
public record QueueRow(
    long messageId,
    String senderEmail,
    String senderName,
    String subject,
    OffsetDateTime sentAt,
    OffsetDateTime receivedAt,
    String status,
    boolean needsAttention,
    String attentionReason,
    List<String> categories,
    Double topCategoryConfidence,
    int documentCount,
    int failedDocumentCount,
    int truncatedDocs,
    int caseCount,
    Double minFieldConfidence,
    int unverifiedEvidence,
    int conflictCount,
    int deadJobs,
    OffsetDateTime lastReviewedAt) {

  /** True when nothing has been extracted yet — the AI pipeline has not run for this message. */
  public boolean awaitingProcessing() {
    return caseCount == 0 && categories.isEmpty();
  }
}
