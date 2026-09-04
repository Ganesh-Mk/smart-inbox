package com.clinevo.inbox.queue.handlers;

import com.clinevo.inbox.ai.AiServiceClient;
import com.clinevo.inbox.audit.AuditService;
import com.clinevo.inbox.ingest.BlobStore;
import com.clinevo.inbox.queue.JobHandler;
import com.clinevo.inbox.queue.JobQueueRepository;
import com.clinevo.inbox.queue.JobType;
import com.clinevo.inbox.queue.QueuedJob;
import com.clinevo.inbox.queue.SubjectType;
import com.clinevo.inbox.repo.ClobSupport;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Parses one document and persists everything the AI service found.
 *
 * <p><b>Idempotent by construction.</b> The handler deletes this document's pages, sections,
 * tables and images before inserting new ones, so a job re-run after a crash — which
 * {@code reap_stale_locks} makes routine (E37) — produces the same rows rather than duplicates.
 *
 * <p><b>A failed document does not fail its message.</b> A password-protected or corrupt PDF is
 * recorded {@code PARSE_FAILED} with a reason, the message is flagged for attention, and the
 * pipeline continues (E7). The email body is still classified, and the item reaches the reviewer
 * marked rather than disappearing.
 *
 * <p>When every document of a message is terminal, this handler trips the completion barrier
 * and enqueues classification (E39).
 */
@Component
public class ParseDocumentHandler implements JobHandler {

  private static final Logger log = LoggerFactory.getLogger(ParseDocumentHandler.class);

  private final JdbcTemplate jdbc;
  private final AiServiceClient ai;
  private final BlobStore blobs;
  private final JobQueueRepository queue;
  private final AuditService audit;

  public ParseDocumentHandler(JdbcTemplate jdbc, AiServiceClient ai, BlobStore blobs,
      JobQueueRepository queue, AuditService audit) {
    this.jdbc = jdbc;
    this.ai = ai;
    this.blobs = blobs;
    this.queue = queue;
    this.audit = audit;
  }

  @Override
  public JobType type() {
    return JobType.PARSE_DOCUMENT;
  }

  @Override
  @Transactional
  public void handle(QueuedJob job) {
    long documentId = job.subjectId();

    Map<String, Object> document;
    try {
      document = jdbc.queryForMap(
          "SELECT id, message_id, source_kind, filename, content_sha256, blob_path"
              + " FROM document WHERE id = ?", documentId);
    } catch (org.springframework.dao.EmptyResultDataAccessException e) {
      log.warn("Document {} no longer exists; nothing to parse", documentId);
      return;
    }

    String sourceKind = (String) document.get("SOURCE_KIND");
    Long messageId = document.get("MESSAGE_ID") == null
        ? null : ((Number) document.get("MESSAGE_ID")).longValue();

    jdbc.update("UPDATE document SET parse_status = 'PARSING' WHERE id = ?", documentId);
    clearPreviousResults(documentId);

    JsonNode response;
    try {
      response = "EMAIL_BODY".equals(sourceKind)
          ? parseBody(messageId)
          : parseAttachment(document);
    } catch (RuntimeException e) {
      // Transport or service failure — retryable, so let the queue apply backoff (E36).
      jdbc.update("UPDATE document SET parse_status = 'PENDING' WHERE id = ?", documentId);
      throw e;
    }

    JsonNode meta = response.path("document");
    String parseStatus = meta.path("parse_status").asText("PARSE_FAILED");

    if (!"PARSED".equals(parseStatus)) {
      String reason = meta.path("parse_error").asText("unknown parse failure");
      recordFailure(documentId, messageId, reason);
    } else {
      persist(documentId, response);
      jdbc.update(
          "UPDATE document SET parse_status = 'PARSED', page_count = ?, doc_rendering = ?,"
              + " doc_genre = ?, primary_language = ?, is_encrypted = ?, truncated = ?,"
              + " parse_ms = ?, parse_error = NULL WHERE id = ?",
          meta.path("page_count").asInt(),
          nullIfBlank(meta.path("rendering").asText()),
          nullIfBlank(meta.path("genre").asText()),
          nullIfBlank(meta.path("primary_language").asText()),
          meta.path("is_encrypted").asBoolean() ? "Y" : "N",
          meta.path("truncated").asBoolean() ? "Y" : "N",
          meta.path("parse_ms").asInt(),
          documentId);

      audit.systemForMessage("DOCUMENT_PARSED", "DOCUMENT", documentId,
          String.format("{\"pages\":%d,\"rendering\":\"%s\",\"genre\":\"%s\",\"cached\":%b}",
              meta.path("page_count").asInt(), meta.path("rendering").asText(),
              meta.path("genre").asText(), meta.path("from_cache").asBoolean()),
          messageId);

      log.info("Parsed document {} ({}): {} page(s), {}/{}, cached={}",
          documentId, document.get("FILENAME"), meta.path("page_count").asInt(),
          meta.path("rendering").asText(), meta.path("genre").asText(),
          meta.path("from_cache").asBoolean());
    }

    recordAiCalls(job.id(), response);

    if (messageId != null) {
      maybeAdvance(messageId);
    }
  }

  // ---- parse ---------------------------------------------------------------------------

  private JsonNode parseBody(Long messageId) {
    Map<String, String> body = jdbc.query(
        "SELECT body_text, body_html FROM inbox_message WHERE id = ?",
        (rs, rowNum) -> Map.of(
            "text", java.util.Objects.requireNonNullElse(ClobSupport.clob(rs, "body_text"), ""),
            "html", java.util.Objects.requireNonNullElse(ClobSupport.clob(rs, "body_html"), "")),
        messageId).stream().findFirst().orElse(Map.of("text", "", "html", ""));
    return ai.parseEmailBody(body.get("text"), body.get("html"));
  }

  private JsonNode parseAttachment(Map<String, Object> document) {
    String sha = (String) document.get("CONTENT_SHA256");
    if (sha == null || !blobs.exists(sha)) {
      throw new IllegalStateException("blob missing for document " + document.get("ID"));
    }
    return ai.parse(blobs.read(sha), (String) document.get("FILENAME"), true);
  }

  // ---- persistence ---------------------------------------------------------------------

  /** Delete-then-insert, keyed on the document: what makes re-running a job safe (E37). */
  private void clearPreviousResults(long documentId) {
    jdbc.update("DELETE FROM document_page WHERE document_id = ?", documentId);
    jdbc.update("DELETE FROM document_section WHERE document_id = ?", documentId);
    jdbc.update("DELETE FROM document_table WHERE document_id = ?", documentId);
    jdbc.update("DELETE FROM document_image WHERE document_id = ?", documentId);
  }

  private void persist(long documentId, JsonNode response) {
    for (JsonNode page : response.path("pages")) {
      jdbc.update(
          "INSERT INTO document_page (document_id, page_no, rendering, genre, language,"
              + " lang_confidence, char_count, has_text_layer, column_count, text_original,"
              + " text_english, legibility, width, height, rotation, render_path,"
              + " span_index_json)"
              + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          documentId,
          page.path("page_no").asInt(),
          nullIfBlank(page.path("rendering").asText()),
          nullIfBlank(page.path("genre").asText()),
          nullIfBlank(page.path("language").asText()),
          page.path("lang_confidence").asDouble(),
          page.path("char_count").asInt(),
          page.path("has_text_layer").asBoolean() ? "Y" : "N",
          page.path("column_count").asInt(1),
          page.path("text_original").asText(""),
          nullIfBlank(page.path("text_english").asText()),
          page.path("legibility").asDouble(1.0),
          page.path("width").asDouble(),
          page.path("height").asDouble(),
          page.path("rotation").asInt(),
          nullIfBlank(page.path("render_path").asText()),
          page.path("span_index_json").asText("[]"));
    }

    for (JsonNode section : response.path("sections")) {
      jdbc.update(
          "INSERT INTO document_section (document_id, page_no, section_index, heading,"
              + " section_kind, char_start, char_end, excluded_from_case)"
              + " VALUES (?,?,?,?,?,?,?,?)",
          documentId,
          section.path("page_no").asInt(),
          section.path("section_index").asInt(),
          truncate(section.path("heading").asText(""), 500),
          nullIfBlank(section.path("section_kind").asText()),
          section.path("char_start").asInt(),
          section.path("char_end").asInt(),
          section.path("excluded_from_case").asBoolean() ? "Y" : "N");
    }

    for (JsonNode table : response.path("tables")) {
      jdbc.update(
          "INSERT INTO document_table (document_id, page_no, table_index, n_rows, n_cols,"
              + " caption, headers_json, rows_json, bbox, extraction_method)"
              + " VALUES (?,?,?,?,?,?,?,?,?,?)",
          documentId,
          table.path("page_no").asInt(),
          table.path("table_index").asInt(),
          table.path("n_rows").asInt(),
          table.path("n_cols").asInt(),
          truncate(table.path("caption").asText(null), 1000),
          table.path("headers").toString(),
          table.path("rows").toString(),
          truncate(table.path("bbox").toString(), 120),
          table.path("extraction_method").asText("PYMUPDF"));
    }

    for (JsonNode image : response.path("images")) {
      // Only images that survived the meaningful-image filter are stored (E19); the rejected
      // ones carry their reason and are useful when explaining why a logo was ignored.
      if (!image.path("keep").asBoolean()) {
        continue;
      }
      jdbc.update(
          "INSERT INTO document_image (document_id, page_no, image_index, xref, bbox, width,"
              + " height, area_ratio, category, description, mentions_defect, mentions_injury,"
              + " confidence, needs_review, blob_path)"
              + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          documentId,
          image.path("page_no").asInt(),
          image.path("image_index").asInt(),
          image.path("xref").asInt(),
          truncate(image.path("bbox").toString(), 120),
          image.path("width").asInt(),
          image.path("height").asInt(),
          image.path("area_ratio").asDouble(),
          nullIfBlank(image.path("category").asText()),
          truncate(image.path("description").asText(null), 4000),
          image.path("mentions_defect").asBoolean() ? "Y" : "N",
          image.path("mentions_injury").asBoolean() ? "Y" : "N",
          image.path("confidence").asDouble(),
          "Y",   // the brief requires human review of image interpretation
          nullIfBlank(image.path("blob_path").asText()));
    }
  }

  private void recordFailure(long documentId, Long messageId, String reason) {
    jdbc.update(
        "UPDATE document SET parse_status = 'PARSE_FAILED', parse_error = ?,"
            + " is_encrypted = ? WHERE id = ?",
        truncate(reason, 2000), reason.startsWith("ENCRYPTED") ? "Y" : "N", documentId);

    if (messageId != null) {
      jdbc.update(
          "UPDATE inbox_message SET needs_attention = 'Y', attention_reason = ?"
              + " WHERE id = ? AND needs_attention = 'N'",
          truncate("A document could not be parsed: " + reason, 500), messageId);
    }

    audit.systemForMessage("DOCUMENT_PARSE_FAILED", "DOCUMENT", documentId,
        "{\"reason\":" + jsonString(reason) + "}", messageId);

    log.warn("Document {} could not be parsed: {} — the message continues without it (E7)",
        documentId, reason);
  }

  private void recordAiCalls(long jobId, JsonNode response) {
    for (JsonNode call : response.path("ai_calls")) {
      jdbc.update(
          "INSERT INTO ai_call_log (job_id, purpose, model, prompt_version, request_json,"
              + " response_json, prompt_tokens, completion_tokens, cached_tokens,"
              + " cache_write_tokens, cost_usd, latency_ms, http_status, retries, repaired)"
              + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          jobId,
          truncate(call.path("purpose").asText("unknown"), 60),
          truncate(call.path("model").asText(""), 100),
          truncate(call.path("prompt_version").asText(""), 40),
          call.path("request_json").toString(),
          call.path("response_json").toString(),
          call.path("prompt_tokens").asInt(),
          call.path("completion_tokens").asInt(),
          call.path("cached_tokens").asInt(),
          call.path("cache_write_tokens").asInt(),
          call.path("cost_usd").asDouble(),
          call.path("latency_ms").asInt(),
          call.path("http_status").asInt(200),
          call.path("retries").asInt(),
          call.path("repaired").asText("N"));
    }
  }

  // ---- E39: the completion barrier -------------------------------------------------------

  /**
   * Enqueue classification once every document of the message is terminal.
   *
   * <p>Terminal means {@code PARSED} <em>or</em> {@code PARSE_FAILED}. A document that failed
   * must not hold the message hostage — it is declared as missing to the classifier instead, so
   * the model can honestly lower its confidence rather than being told nothing is wrong.
   */
  private void maybeAdvance(long messageId) {
    if (!queue.allDocumentsTerminal(messageId)) {
      return;
    }
    Integer alreadyQueued = jdbc.queryForObject(
        "SELECT COUNT(*) FROM job WHERE job_type = 'CLASSIFY_MESSAGE' AND subject_id = ?"
            + " AND state IN ('PENDING','RUNNING','DONE')", Integer.class, messageId);
    if (alreadyQueued != null && alreadyQueued > 0) {
      return;   // another document's handler already tripped the barrier
    }

    jdbc.update("UPDATE inbox_message SET status = 'PARSED' WHERE id = ?", messageId);
    queue.enqueue(JobType.CLASSIFY_MESSAGE, SubjectType.MESSAGE, messageId);
    log.info("All documents terminal for message {} — classification enqueued (E39)", messageId);
  }

  // ---- helpers ---------------------------------------------------------------------------

  static String nullIfBlank(String value) {
    return value == null || value.isBlank() || "null".equals(value) ? null : value;
  }

  static String truncate(String value, int max) {
    if (value == null) {
      return null;
    }
    return value.length() <= max ? value : value.substring(0, max);
  }

  static String jsonString(String value) {
    if (value == null) {
      return "null";
    }
    return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"")
        .replace("\n", " ").replace("\r", " ") + "\"";
  }
}
