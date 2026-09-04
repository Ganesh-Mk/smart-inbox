package com.clinevo.inbox.queue.handlers;

import static com.clinevo.inbox.queue.handlers.ParseDocumentHandler.truncate;

import com.clinevo.inbox.ai.AiServiceClient;
import com.clinevo.inbox.audit.AuditService;
import com.clinevo.inbox.queue.JobHandler;
import com.clinevo.inbox.queue.JobType;
import com.clinevo.inbox.queue.QueuedJob;
import com.clinevo.inbox.repo.ClobSupport;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * The last stage: summarise each document, set the attention flags, and hand the message to the
 * reviewer.
 *
 * <p>The flags are what make the queue useful. A reviewer opening the queue should see, without
 * clicking into anything, which messages need them and why: a citation that could not be proved,
 * two sources disagreeing, a document that would not open, a truncated file. Sorting by "worst
 * first" is only meaningful because these are computed here.
 */
@Component
public class FinaliseMessageHandler implements JobHandler {

  private static final Logger log = LoggerFactory.getLogger(FinaliseMessageHandler.class);

  private final JdbcTemplate jdbc;
  private final AiServiceClient ai;
  private final AuditService audit;

  public FinaliseMessageHandler(JdbcTemplate jdbc, AiServiceClient ai, AuditService audit) {
    this.jdbc = jdbc;
    this.ai = ai;
    this.audit = audit;
  }

  @Override
  public JobType type() {
    return JobType.FINALISE_MESSAGE;
  }

  @Override
  @Transactional
  public void handle(QueuedJob job) {
    long messageId = job.subjectId();

    summariseDocuments(messageId, job.id());
    String attention = computeAttention(messageId);

    jdbc.update(
        "UPDATE inbox_message SET status = 'READY_FOR_REVIEW', needs_attention = ?,"
            + " attention_reason = ? WHERE id = ?",
        attention == null ? "N" : "Y", truncate(attention, 500), messageId);

    audit.systemForMessage("MESSAGE_READY_FOR_REVIEW", "INBOX_MESSAGE", messageId,
        "{\"needs_attention\":" + (attention != null)
            + ",\"reason\":" + ParseDocumentHandler.jsonString(attention) + "}",
        messageId);

    log.info("Message {} ready for review{}", messageId,
        attention == null ? "" : " — needs attention: " + attention);
  }

  /**
   * R7: a summary and a relevance verdict per document.
   *
   * <p>A failure here does not fail the message. The summary is a convenience for the reviewer,
   * not part of the case record, and losing the whole message because a summary call timed out
   * would be a poor trade.
   */
  private void summariseDocuments(long messageId, long jobId) {
    List<Map<String, Object>> documents = jdbc.queryForList(
        "SELECT id, filename FROM document WHERE message_id = ? AND parse_status = 'PARSED'"
            + " AND id NOT IN (SELECT document_id FROM document_summary)", messageId);

    for (Map<String, Object> document : documents) {
      long documentId = ((Number) document.get("ID")).longValue();
      // Concatenated in Java rather than with LISTAGG(TO_CHAR(...)): LISTAGG builds a
      // VARCHAR2 and would fail on any document over 4,000 characters (see ClobSupport).
      List<String> pageTexts = jdbc.query(
          "SELECT text_original FROM document_page WHERE document_id = ? ORDER BY page_no",
          (rs, rowNum) -> ClobSupport.clob(rs, "text_original"), documentId);
      String text = pageTexts.stream()
          .filter(java.util.Objects::nonNull)
          .reduce((a, b) -> a + "\n\n" + b)
          .orElse("");

      if (text.isBlank()) {
        continue;
      }

      try {
        JsonNode response = ai.summarise(text);
        jdbc.update(
            "INSERT INTO document_summary (document_id, summary_text, sentence_count,"
                + " relevance, relevance_reason, model, prompt_version)"
                + " VALUES (?,?,?,?,?,?,?)",
            documentId,
            response.path("summary").asText(""),
            response.path("sentence_count").asInt(),
            response.path("relevance").asText("POSSIBLY"),
            truncate(response.path("relevance_reason").asText(""), 2000),
            "anthropic/claude-haiku-4.5",
            "P6_summarise@v1");

        for (JsonNode call : response.path("ai_calls")) {
          jdbc.update(
              "INSERT INTO ai_call_log (job_id, purpose, model, prompt_version, request_json,"
                  + " response_json, prompt_tokens, completion_tokens, cached_tokens,"
                  + " cache_write_tokens, cost_usd, latency_ms, http_status, retries, repaired)"
                  + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              jobId, truncate(call.path("purpose").asText("summarise"), 60),
              truncate(call.path("model").asText(""), 100),
              truncate(call.path("prompt_version").asText(""), 40),
              call.path("request_json").toString(), call.path("response_json").toString(),
              call.path("prompt_tokens").asInt(), call.path("completion_tokens").asInt(),
              call.path("cached_tokens").asInt(), call.path("cache_write_tokens").asInt(),
              call.path("cost_usd").asDouble(), call.path("latency_ms").asInt(),
              call.path("http_status").asInt(200), call.path("retries").asInt(),
              call.path("repaired").asText("N"));
        }
      } catch (RuntimeException e) {
        log.warn("Could not summarise document {}: {}", documentId, e.getMessage());
      }
    }
  }

  /** Why this message needs a human's attention, or {@code null} when it does not. */
  private String computeAttention(long messageId) {
    StringBuilder reasons = new StringBuilder();

    Integer failedDocuments = jdbc.queryForObject(
        "SELECT COUNT(*) FROM document WHERE message_id = ? AND parse_status = 'PARSE_FAILED'",
        Integer.class, messageId);
    if (failedDocuments != null && failedDocuments > 0) {
      append(reasons, failedDocuments + " document(s) could not be parsed");
    }

    Integer unverified = jdbc.queryForObject(
        "SELECT COUNT(*) FROM field_evidence e JOIN extracted_field f ON f.id = e.field_id"
            + " JOIN case_record c ON c.id = f.case_id"
            + " WHERE c.message_id = ? AND e.verified = 'N' AND f.superseded_by IS NULL",
        Integer.class, messageId);
    if (unverified != null && unverified > 0) {
      // The differentiator, surfaced: the system reporting its own unproven citations.
      append(reasons, unverified + " citation(s) could not be verified against the source");
    }

    Integer conflicts = jdbc.queryForObject(
        "SELECT COUNT(*) FROM extracted_field f JOIN case_record c ON c.id = f.case_id"
            + " WHERE c.message_id = ? AND f.status = 'CONFLICT' AND f.superseded_by IS NULL",
        Integer.class, messageId);
    if (conflicts != null && conflicts > 0) {
      append(reasons, conflicts + " field(s) where sources disagree");
    }

    Integer truncated = jdbc.queryForObject(
        "SELECT COUNT(*) FROM document WHERE message_id = ? AND truncated = 'Y'",
        Integer.class, messageId);
    if (truncated != null && truncated > 0) {
      append(reasons, truncated + " document(s) exceeded the page cap and were truncated");
    }

    Integer incomplete = jdbc.queryForObject(
        "SELECT COUNT(*) FROM classification WHERE subject_type = 'MESSAGE' AND subject_id = ?"
            + " AND category = 'ICSR_INCOMPLETE' AND superseded_by IS NULL",
        Integer.class, messageId);
    if (incomplete != null && incomplete > 0) {
      append(reasons, "ICSR is missing one or more minimum criteria");
    }

    Integer lowConfidence = jdbc.queryForObject(
        "SELECT COUNT(*) FROM extracted_field f JOIN case_record c ON c.id = f.case_id"
            + " WHERE c.message_id = ? AND f.superseded_by IS NULL"
            + " AND f.status IN ('STATED','UNCERTAIN') AND f.confidence < 0.4",
        Integer.class, messageId);
    if (lowConfidence != null && lowConfidence > 0) {
      append(reasons, lowConfidence + " field(s) below 0.40 confidence");
    }

    return reasons.length() == 0 ? null : reasons.toString();
  }

  private static void append(StringBuilder builder, String reason) {
    if (builder.length() > 0) {
      builder.append("; ");
    }
    builder.append(reason);
  }
}
