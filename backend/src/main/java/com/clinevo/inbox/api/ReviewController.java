package com.clinevo.inbox.api;

import com.clinevo.inbox.audit.AuditService;
import com.clinevo.inbox.queue.JobQueueRepository;
import com.clinevo.inbox.queue.JobType;
import com.clinevo.inbox.queue.SubjectType;
import com.clinevo.inbox.repo.ClobSupport;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.security.Principal;
import java.sql.CallableStatement;
import java.sql.Types;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The reviewer's API.
 *
 * <p>Reads come straight from {@code V_REVIEW_QUEUE} and a handful of joins; writes go through
 * {@code PKG_REVIEW}, which applies an override as a *supersede* and writes the audit row in one
 * transaction. Nothing here updates an AI-authored row in place — that is the whole point of
 * E40, and putting the logic in PL/SQL means a future code path cannot forget it.
 */
@RestController
@RequestMapping("/api")
public class ReviewController {

  private static final Logger log = LoggerFactory.getLogger(ReviewController.class);
  private static final ObjectMapper MAPPER = new ObjectMapper();

  private final JdbcTemplate jdbc;
  private final JobQueueRepository queue;
  private final AuditService audit;

  public ReviewController(JdbcTemplate jdbc, JobQueueRepository queue, AuditService audit) {
    this.jdbc = jdbc;
    this.queue = queue;
    this.audit = audit;
  }

  // ---- queue -------------------------------------------------------------------------------

  /**
   * The review queue, worst first.
   *
   * <p>"Worst first" is the ordering a reviewer's day actually has: anything needing attention,
   * then the lowest field confidence, then the oldest. Sorting by arrival time would bury the
   * cases most likely to be wrong under a pile of routine ones.
   */
  @GetMapping("/messages")
  public Map<String, Object> queue(
      @RequestParam(required = false) String status,
      @RequestParam(required = false) String category,
      @RequestParam(required = false) Boolean flagged,
      @RequestParam(required = false) String q,
      @RequestParam(defaultValue = "0") int page,
      @RequestParam(defaultValue = "50") int size) {

    StringBuilder where = new StringBuilder(" WHERE 1=1");
    List<Object> args = new ArrayList<>();

    if (status != null && !status.isBlank()) {
      where.append(" AND status = ?");
      args.add(status);
    }
    if (category != null && !category.isBlank()) {
      where.append(" AND categories LIKE ?");
      args.add("%" + category + "%");
    }
    if (Boolean.TRUE.equals(flagged)) {
      where.append(" AND (needs_attention = 'Y' OR unverified_evidence > 0 OR conflict_count > 0)");
    }
    if (q != null && !q.isBlank()) {
      where.append(" AND (LOWER(subject) LIKE ? OR LOWER(sender_email) LIKE ?)");
      args.add("%" + q.toLowerCase() + "%");
      args.add("%" + q.toLowerCase() + "%");
    }

    Integer total = jdbc.queryForObject(
        "SELECT COUNT(*) FROM v_review_queue" + where, Integer.class, args.toArray());

    List<Object> pageArgs = new ArrayList<>(args);
    pageArgs.add(page * size);
    pageArgs.add(size);

    List<Map<String, Object>> rows = jdbc.queryForList(
        "SELECT * FROM v_review_queue" + where
            + " ORDER BY needs_attention DESC,"
            + "   NVL(min_field_confidence, 2) ASC,"     // NULL means nothing extracted yet
            + "   received_at ASC"
            + " OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
        pageArgs.toArray());

    return Map.of(
        "total", total == null ? 0 : total,
        "page", page,
        "size", size,
        "rows", rows.stream().map(ReviewController::normaliseRow).toList());
  }

  /** Counts for the queue header and the batch report. */
  @GetMapping("/stats/overview")
  public Map<String, Object> overview() {
    Map<String, Object> stats = new HashMap<>();
    stats.put("messages", count("SELECT COUNT(*) FROM inbox_message"));
    stats.put("documents", count("SELECT COUNT(*) FROM document"));
    stats.put("attachments", count("SELECT COUNT(*) FROM message_attachment"));
    stats.put("cases", count("SELECT COUNT(*) FROM case_record"));
    stats.put("readyForReview",
        count("SELECT COUNT(*) FROM inbox_message WHERE status = 'READY_FOR_REVIEW'"));
    stats.put("reviewed", count("SELECT COUNT(*) FROM inbox_message WHERE status = 'REVIEWED'"));
    stats.put("needsAttention",
        count("SELECT COUNT(*) FROM inbox_message WHERE needs_attention = 'Y'"));
    stats.put("pendingJobs", count("SELECT COUNT(*) FROM job WHERE state = 'PENDING'"));
    stats.put("runningJobs", count("SELECT COUNT(*) FROM job WHERE state = 'RUNNING'"));
    stats.put("deadJobs", count("SELECT COUNT(*) FROM job WHERE state = 'DEAD'"));
    stats.put("parseFailed",
        count("SELECT COUNT(*) FROM document WHERE parse_status = 'PARSE_FAILED'"));

    // The differentiator, as a single number: what fraction of asserted facts carry a quote
    // that code could actually find in the source.
    Map<String, Object> verification = jdbc.queryForMap(
        "SELECT COUNT(*) AS asserted,"
            + " SUM(CASE WHEN verified = 'Y' THEN 1 ELSE 0 END) AS verified"
            + " FROM field_evidence e JOIN extracted_field f ON f.id = e.field_id"
            + " WHERE f.status IN ('STATED','UNCERTAIN','CONFLICT') AND f.superseded_by IS NULL");
    long asserted = toLong(verification.get("ASSERTED"));
    long verified = toLong(verification.get("VERIFIED"));
    stats.put("evidenceAsserted", asserted);
    stats.put("evidenceVerified", verified);
    stats.put("verificationRate", asserted == 0 ? 0.0 : Math.round(1000.0 * verified / asserted) / 1000.0);

    Map<String, Object> cost = jdbc.queryForMap(
        "SELECT NVL(SUM(cost_usd),0) AS total_cost, COUNT(*) AS calls,"
            + " NVL(SUM(cached_tokens),0) AS cached, NVL(SUM(prompt_tokens),0) AS prompt"
            + " FROM ai_call_log");
    stats.put("aiCalls", toLong(cost.get("CALLS")));
    stats.put("totalCostUsd", cost.get("TOTAL_COST"));
    long prompt = toLong(cost.get("PROMPT"));
    stats.put("cacheHitRate",
        prompt == 0 ? 0.0 : Math.round(1000.0 * toLong(cost.get("CACHED")) / prompt) / 1000.0);

    return stats;
  }

  // ---- detail ------------------------------------------------------------------------------

  @GetMapping("/messages/{id}")
  public Map<String, Object> detail(@PathVariable long id) {
    Map<String, Object> message = jdbc.query(
        "SELECT id, dedupe_key, message_id_hdr, sender_email, sender_name, subject, sent_at,"
            + " received_at, body_text, body_charset, quoted_offset,"
            + " status, needs_attention, attention_reason"
            + " FROM inbox_message WHERE id = ?",
        (rs, rowNum) -> {
          Map<String, Object> row = new HashMap<>();
          row.put("ID", rs.getLong("id"));
          row.put("SENDER_EMAIL", rs.getString("sender_email"));
          row.put("SENDER_NAME", rs.getString("sender_name"));
          row.put("SUBJECT", rs.getString("subject"));
          row.put("SENT_AT", rs.getTimestamp("sent_at"));
          row.put("RECEIVED_AT", rs.getTimestamp("received_at"));
          row.put("BODY_TEXT", ClobSupport.clob(rs, "body_text"));
          row.put("QUOTED_OFFSET", rs.getObject("quoted_offset"));
          row.put("STATUS", rs.getString("status"));
          row.put("NEEDS_ATTENTION", rs.getString("needs_attention"));
          row.put("ATTENTION_REASON", rs.getString("attention_reason"));
          return row;
        }, id).stream().findFirst().orElseThrow(() -> new ResponseStatusException(
            HttpStatus.NOT_FOUND, "No message with id " + id));

    List<Map<String, Object>> documents = jdbc.queryForList(
        "SELECT id, source_kind, filename, page_count, doc_rendering, doc_genre,"
            + " primary_language, is_encrypted, truncated, parse_status, parse_error, parse_ms"
            + " FROM document WHERE message_id = ? ORDER BY id", id);

    for (Map<String, Object> document : documents) {
      long documentId = toLong(document.get("ID"));
      document.put("pages", jdbc.query(
          "SELECT page_no, rendering, genre, language, lang_confidence, char_count,"
              + " column_count, legibility, width, height, render_path,"
              + " text_original, text_english"
              + " FROM document_page WHERE document_id = ? ORDER BY page_no",
          (rs, rowNum) -> {
            Map<String, Object> row = new HashMap<>();
            row.put("PAGE_NO", rs.getInt("page_no"));
            row.put("RENDERING", rs.getString("rendering"));
            row.put("GENRE", rs.getString("genre"));
            row.put("LANGUAGE", rs.getString("language"));
            row.put("LANG_CONFIDENCE", rs.getDouble("lang_confidence"));
            row.put("CHAR_COUNT", rs.getInt("char_count"));
            row.put("COLUMN_COUNT", rs.getInt("column_count"));
            row.put("LEGIBILITY", rs.getDouble("legibility"));
            row.put("WIDTH", rs.getDouble("width"));
            row.put("HEIGHT", rs.getDouble("height"));
            row.put("RENDER_PATH", rs.getString("render_path"));
            row.put("TEXT_ORIGINAL", ClobSupport.clob(rs, "text_original"));
            row.put("TEXT_ENGLISH", ClobSupport.clob(rs, "text_english"));
            return row;
          }, documentId));
      document.put("tables", jdbc.query(
          "SELECT page_no, table_index, n_rows, n_cols, caption, headers_json, rows_json,"
              + " extraction_method FROM document_table WHERE document_id = ?"
              + " ORDER BY page_no, table_index",
          (rs, rowNum) -> {
            Map<String, Object> row = new HashMap<>();
            row.put("PAGE_NO", rs.getInt("page_no"));
            row.put("TABLE_INDEX", rs.getInt("table_index"));
            row.put("N_ROWS", rs.getInt("n_rows"));
            row.put("N_COLS", rs.getInt("n_cols"));
            row.put("CAPTION", rs.getString("caption"));
            row.put("HEADERS_JSON", ClobSupport.clob(rs, "headers_json"));
            row.put("ROWS_JSON", ClobSupport.clob(rs, "rows_json"));
            row.put("EXTRACTION_METHOD", rs.getString("extraction_method"));
            return row;
          }, documentId));
      document.put("images", jdbc.queryForList(
          "SELECT page_no, image_index, bbox, category, description, mentions_defect,"
              + " mentions_injury, confidence, needs_review FROM document_image"
              + " WHERE document_id = ? ORDER BY page_no, image_index", documentId));
      document.put("sections", jdbc.queryForList(
          "SELECT page_no, heading, section_kind, char_start, char_end, excluded_from_case"
              + " FROM document_section WHERE document_id = ? ORDER BY section_index", documentId));
      List<Map<String, Object>> summary = jdbc.query(
          "SELECT summary_text, sentence_count, relevance, relevance_reason"
              + " FROM document_summary WHERE document_id = ?",
          (rs, rowNum) -> {
            Map<String, Object> row = new HashMap<>();
            row.put("SUMMARY_TEXT", ClobSupport.clob(rs, "summary_text"));
            row.put("SENTENCE_COUNT", rs.getInt("sentence_count"));
            row.put("RELEVANCE", rs.getString("relevance"));
            row.put("RELEVANCE_REASON", rs.getString("relevance_reason"));
            return row;
          }, documentId);
      document.put("summary", summary.isEmpty() ? null : summary.get(0));
    }

    List<Map<String, Object>> classifications = jdbc.queryForList(
        "SELECT id, category, confidence, reason, decided_by, decided_by_user, created_at"
            + " FROM classification WHERE subject_type = 'MESSAGE' AND subject_id = ?"
            + " AND superseded_by IS NULL ORDER BY confidence DESC", id);

    for (Map<String, Object> classification : classifications) {
      List<Map<String, Object>> validity = jdbc.queryForList(
          "SELECT has_patient, patient_confidence, patient_evidence, has_reporter,"
              + " reporter_confidence, reporter_evidence, has_product, product_confidence,"
              + " product_evidence, has_event, event_confidence, event_evidence,"
              + " elements_present, missing_elements_json FROM icsr_validity"
              + " WHERE classification_id = ?", toLong(classification.get("ID")));
      classification.put("icsrValidity", validity.isEmpty() ? null : validity.get(0));
    }

    List<Map<String, Object>> cases = jdbc.query(
        "SELECT id, case_index, case_type, patient_descriptor, narrative, is_serious,"
            + " seriousness_json, confidence FROM case_record"
            + " WHERE message_id = ? ORDER BY case_index",
        (rs, rowNum) -> {
          Map<String, Object> row = new HashMap<>();
          row.put("ID", rs.getLong("id"));
          row.put("CASE_INDEX", rs.getInt("case_index"));
          row.put("CASE_TYPE", rs.getString("case_type"));
          row.put("PATIENT_DESCRIPTOR", rs.getString("patient_descriptor"));
          row.put("NARRATIVE", ClobSupport.clob(rs, "narrative"));
          row.put("IS_SERIOUS", rs.getString("is_serious"));
          row.put("SERIOUSNESS_JSON", rs.getString("seriousness_json"));
          row.put("CONFIDENCE", rs.getDouble("confidence"));
          return row;
        }, id);

    for (Map<String, Object> caseRow : cases) {
      long caseId = toLong(caseRow.get("ID"));
      List<Map<String, Object>> fields = jdbc.query(
          "SELECT id, field_group, field_path, field_index, value_text, value_json, unit,"
              + " raw_text, status, confidence, confidence_pre_adjust, adjust_reason,"
              + " decided_by, decided_by_user"
              + " FROM extracted_field WHERE case_id = ? AND superseded_by IS NULL"
              + " ORDER BY field_group, field_index, field_path",
          (rs, rowNum) -> {
            Map<String, Object> row = new HashMap<>();
            row.put("ID", rs.getLong("id"));
            row.put("FIELD_GROUP", rs.getString("field_group"));
            row.put("FIELD_PATH", rs.getString("field_path"));
            row.put("FIELD_INDEX", rs.getInt("field_index"));
            row.put("VALUE_TEXT", rs.getString("value_text"));
            row.put("VALUE_JSON", ClobSupport.clob(rs, "value_json"));
            row.put("UNIT", rs.getString("unit"));
            row.put("RAW_TEXT", rs.getString("raw_text"));
            row.put("STATUS", rs.getString("status"));
            row.put("CONFIDENCE", rs.getDouble("confidence"));
            row.put("CONFIDENCE_PRE_ADJUST", rs.getDouble("confidence_pre_adjust"));
            row.put("ADJUST_REASON", rs.getString("adjust_reason"));
            row.put("DECIDED_BY", rs.getString("decided_by"));
            row.put("DECIDED_BY_USER", rs.getString("decided_by_user"));
            return row;
          }, caseId);
      for (Map<String, Object> field : fields) {
        field.put("evidence", jdbc.queryForList(
            "SELECT id, source_type, document_id, page_no, quote, char_start, char_end, bbox,"
                + " verified, verify_method, match_score FROM field_evidence"
                + " WHERE field_id = ?", toLong(field.get("ID"))));
      }
      caseRow.put("fields", fields);
    }

    List<Map<String, Object>> attachments = jdbc.queryForList(
        "SELECT filename, declared_type, sniffed_type, size_bytes, sha256, nesting_level,"
            + " processed, skip_reason FROM message_attachment WHERE message_id = ?"
            + " ORDER BY id", id);

    Map<String, Object> out = new HashMap<>(normaliseRow(message));
    out.put("documents", documents);
    out.put("classifications", classifications);
    out.put("cases", cases);
    out.put("attachments", attachments);
    return out;
  }

  /** The rendered page PNG the highlight overlay is drawn on. */
  @GetMapping("/documents/{documentId}/pages/{pageNo}/image")
  public ResponseEntity<byte[]> pageImage(
      @PathVariable long documentId, @PathVariable int pageNo) {
    List<String> paths = jdbc.queryForList(
        "SELECT render_path FROM document_page WHERE document_id = ? AND page_no = ?",
        String.class, documentId, pageNo);
    if (paths.isEmpty() || paths.get(0) == null || paths.get(0).isBlank()) {
      return ResponseEntity.notFound().build();
    }
    try {
      java.nio.file.Path path = java.nio.file.Path.of("data").resolve(paths.get(0));
      if (!java.nio.file.Files.exists(path)) {
        path = java.nio.file.Path.of("..").resolve("data").resolve(paths.get(0));
      }
      byte[] bytes = java.nio.file.Files.readAllBytes(path);
      return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).body(bytes);
    } catch (java.io.IOException e) {
      log.warn("Page image not readable for document {} page {}: {}",
          documentId, pageNo, e.getMessage());
      return ResponseEntity.notFound().build();
    }
  }

  @GetMapping("/messages/{id}/audit")
  public List<Map<String, Object>> auditTrail(@PathVariable long id) {
    return jdbc.query(
        "SELECT id, actor, actor_type, action, entity_type, entity_id, before_json, after_json,"
            + " occurred_at FROM audit_event WHERE message_id = ?"
            + " ORDER BY occurred_at DESC, id DESC",
        (rs, rowNum) -> {
          Map<String, Object> row = new HashMap<>();
          row.put("ID", rs.getLong("id"));
          row.put("ACTOR", rs.getString("actor"));
          row.put("ACTOR_TYPE", rs.getString("actor_type"));
          row.put("ACTION", rs.getString("action"));
          row.put("ENTITY_TYPE", rs.getString("entity_type"));
          row.put("ENTITY_ID", rs.getObject("entity_id"));
          row.put("BEFORE_JSON", ClobSupport.clob(rs, "before_json"));
          row.put("AFTER_JSON", ClobSupport.clob(rs, "after_json"));
          row.put("OCCURRED_AT", rs.getTimestamp("occurred_at"));
          return row;
        }, id);
  }

  /** The traceability money shot: the exact request and response behind one AI decision. */
  @GetMapping("/ai-calls/{id}")
  public Map<String, Object> aiCall(@PathVariable long id) {
    return jdbc.query(
        "SELECT id, job_id, purpose, model, prompt_version, request_json, response_json,"
            + " prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens, cost_usd,"
            + " latency_ms, http_status, retries, repaired, created_at"
            + " FROM ai_call_log WHERE id = ?",
        (rs, rowNum) -> {
          Map<String, Object> row = new HashMap<>();
          row.put("ID", rs.getLong("id"));
          row.put("PURPOSE", rs.getString("purpose"));
          row.put("MODEL", rs.getString("model"));
          row.put("PROMPT_VERSION", rs.getString("prompt_version"));
          // The traceability money shot: the exact request and response, in full.
          row.put("REQUEST_JSON", ClobSupport.clob(rs, "request_json"));
          row.put("RESPONSE_JSON", ClobSupport.clob(rs, "response_json"));
          row.put("PROMPT_TOKENS", rs.getInt("prompt_tokens"));
          row.put("COMPLETION_TOKENS", rs.getInt("completion_tokens"));
          row.put("CACHED_TOKENS", rs.getInt("cached_tokens"));
          row.put("COST_USD", rs.getDouble("cost_usd"));
          row.put("LATENCY_MS", rs.getInt("latency_ms"));
          row.put("REPAIRED", rs.getString("repaired"));
          row.put("CREATED_AT", rs.getTimestamp("created_at"));
          return row;
        }, id).stream().findFirst().orElseThrow(() -> new ResponseStatusException(
            HttpStatus.NOT_FOUND, "No AI call with id " + id));
  }

  @GetMapping("/messages/{id}/ai-calls")
  public List<Map<String, Object>> aiCallsForMessage(@PathVariable long id) {
    return jdbc.queryForList(
        "SELECT l.id, l.purpose, l.model, l.prompt_version, l.prompt_tokens, l.completion_tokens,"
            + " l.cached_tokens, l.cost_usd, l.latency_ms, l.repaired, l.created_at"
            + " FROM ai_call_log l JOIN job j ON j.id = l.job_id"
            + " WHERE (j.subject_type = 'MESSAGE' AND j.subject_id = ?)"
            + "    OR (j.subject_type = 'DOCUMENT' AND j.subject_id IN"
            + "        (SELECT id FROM document WHERE message_id = ?))"
            + " ORDER BY l.created_at", id, id);
  }

  // ---- reviewer actions ---------------------------------------------------------------------

  /** Accept, override or reject. Applied as a supersede, never an overwrite (E40). */
  @PostMapping("/messages/{id}/review")
  public Map<String, Object> review(
      @PathVariable long id,
      @RequestBody Map<String, Object> body,
      Principal principal) throws Exception {

    String decision = String.valueOf(body.getOrDefault("decision", "ACCEPT")).toUpperCase();
    if (!List.of("ACCEPT", "OVERRIDE", "REJECT").contains(decision)) {
      throw new IllegalArgumentException("decision must be ACCEPT, OVERRIDE or REJECT");
    }
    @SuppressWarnings("unchecked")
    List<String> categories = (List<String>) body.getOrDefault("categories", List.of());
    String notes = (String) body.get("notes");
    String reviewer = principal == null ? "anonymous" : principal.getName();
    String categoriesJson = MAPPER.writeValueAsString(categories);

    jdbc.execute((ConnectionCallback<Void>) connection -> {
      try (CallableStatement cs = connection.prepareCall(
          "BEGIN pkg_review.apply_override(?, ?, ?, ?, ?, ?); END;")) {
        cs.setLong(1, id);
        cs.setString(2, reviewer);
        cs.setString(3, categoriesJson);
        cs.setString(4, decision);
        cs.setString(5, notes);
        cs.setString(6, null);
        cs.execute();
      }
      return null;
    });

    log.info("Message {} {} by {}", id, decision, reviewer);
    return Map.of("messageId", id, "decision", decision, "reviewer", reviewer,
        "categories", categories);
  }

  /** Override one extracted field. The AI's row survives and points at the replacement (E40). */
  @PatchMapping("/cases/{caseId}/fields/{fieldId}")
  public Map<String, Object> overrideField(
      @PathVariable long caseId,
      @PathVariable long fieldId,
      @RequestBody Map<String, Object> body,
      Principal principal) {

    String value = String.valueOf(body.getOrDefault("value", ""));
    String status = String.valueOf(body.getOrDefault("status", "STATED")).toUpperCase();
    String note = (String) body.get("note");
    String reviewer = principal == null ? "anonymous" : principal.getName();

    Long newId = jdbc.execute((ConnectionCallback<Long>) connection -> {
      try (CallableStatement cs = connection.prepareCall(
          "BEGIN pkg_review.override_field(?, ?, ?, ?, ?, ?, ?); END;")) {
        cs.setLong(1, fieldId);
        cs.setString(2, reviewer);
        cs.setString(3, value);
        cs.setString(4, status);
        cs.setString(5, note);
        cs.setString(6, null);
        cs.registerOutParameter(7, Types.NUMERIC);
        cs.execute();
        return cs.getLong(7);
      }
    });

    log.info("Field {} overridden by {} (new row {})", fieldId, reviewer, newId);
    return Map.of("originalFieldId", fieldId, "newFieldId", newId, "reviewer", reviewer);
  }

  /** Re-run the pipeline for a message, from parsing. Useful for a demo and for recovery. */
  @PostMapping("/messages/{id}/reprocess")
  public Map<String, Object> reprocess(@PathVariable long id, Principal principal) {
    List<Long> documentIds = jdbc.queryForList(
        "SELECT id FROM document WHERE message_id = ?", Long.class, id);
    jdbc.update("UPDATE document SET parse_status = 'PENDING' WHERE message_id = ?", id);
    jdbc.update("UPDATE inbox_message SET status = 'PARSING', needs_attention = 'N',"
        + " attention_reason = NULL WHERE id = ?", id);

    for (Long documentId : documentIds) {
      queue.enqueue(JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, documentId);
    }

    audit.reviewer(principal == null ? "anonymous" : principal.getName(),
        "MESSAGE_REPROCESS", "INBOX_MESSAGE", id, null,
        "{\"documents\":" + documentIds.size() + "}", id);

    return Map.of("messageId", id, "documentsRequeued", documentIds.size());
  }

  // ---- helpers --------------------------------------------------------------------------------

  private long count(String sql) {
    Integer value = jdbc.queryForObject(sql, Integer.class);
    return value == null ? 0 : value;
  }

  private static long toLong(Object value) {
    return value instanceof Number number ? number.longValue() : 0L;
  }

  /** Oracle returns UPPERCASE column names; the UI wants lowerCamelCase. */
  private static Map<String, Object> normaliseRow(Map<String, Object> row) {
    Map<String, Object> out = new HashMap<>();
    row.forEach((key, value) -> out.put(toCamel(key), value));
    return out;
  }

  private static String toCamel(String column) {
    String[] parts = column.toLowerCase().split("_");
    StringBuilder builder = new StringBuilder(parts[0]);
    for (int i = 1; i < parts.length; i++) {
      if (parts[i].isEmpty()) {
        continue;
      }
      builder.append(Character.toUpperCase(parts[i].charAt(0))).append(parts[i].substring(1));
    }
    return builder.toString();
  }
}
