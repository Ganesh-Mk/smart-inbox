package com.clinevo.inbox.queue.handlers;

import static com.clinevo.inbox.queue.handlers.ParseDocumentHandler.nullIfBlank;
import static com.clinevo.inbox.queue.handlers.ParseDocumentHandler.truncate;

import com.clinevo.inbox.ai.AiServiceClient;
import com.clinevo.inbox.audit.AuditService;
import com.clinevo.inbox.queue.JobHandler;
import com.clinevo.inbox.queue.JobQueueRepository;
import com.clinevo.inbox.queue.JobType;
import com.clinevo.inbox.queue.QueuedJob;
import com.clinevo.inbox.queue.SubjectType;
import com.clinevo.inbox.repo.ClobSupport;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Classifies a message: each unit on its own, then the message as the union (E25).
 *
 * <p>The unit list is the email body plus every parsed document. A document that failed to parse
 * is <em>declared</em> to the classifier rather than silently omitted, so the model knows part of
 * the evidence is missing and can lower its confidence honestly (E39).
 *
 * <p>Where the message has real content, one {@code EXTRACT_CASE} job is enqueued per matched
 * category. A {@code NOT_RELEVANT} message skips extraction entirely and goes straight to
 * finalisation — there is nothing to extract, and spending on it would be waste.
 */
@Component
public class ClassifyMessageHandler implements JobHandler {

  private static final Logger log = LoggerFactory.getLogger(ClassifyMessageHandler.class);

  private final JdbcTemplate jdbc;
  private final AiServiceClient ai;
  private final JobQueueRepository queue;
  private final AuditService audit;

  public ClassifyMessageHandler(JdbcTemplate jdbc, AiServiceClient ai,
      JobQueueRepository queue, AuditService audit) {
    this.jdbc = jdbc;
    this.ai = ai;
    this.queue = queue;
    this.audit = audit;
  }

  @Override
  public JobType type() {
    return JobType.CLASSIFY_MESSAGE;
  }

  @Override
  @Transactional
  public void handle(QueuedJob job) {
    long messageId = job.subjectId();

    jdbc.update("UPDATE inbox_message SET status = 'CLASSIFYING' WHERE id = ?", messageId);

    List<Map<String, Object>> units = buildUnits(messageId);
    if (units.isEmpty()) {
      log.warn("Message {} has no readable content at all; marking NOT_RELEVANT", messageId);
      insertClassification(messageId, "MESSAGE", messageId, "NOT_RELEVANT", 0.5,
          "No readable content in the body or any attachment.", "RULE", null);
      finish(messageId, List.of());
      return;
    }

    JsonNode response = ai.classify(units, describeMessage(messageId));

    // Idempotent: a re-run replaces this message's AI classifications rather than adding to
    // them. Reviewer rows are never touched — they are the human record (E40).
    jdbc.update(
        "DELETE FROM classification WHERE subject_type = 'MESSAGE' AND subject_id = ?"
            + " AND decided_by <> 'REVIEWER'", messageId);
    jdbc.update(
        "DELETE FROM classification WHERE subject_type = 'DOCUMENT' AND decided_by <> 'REVIEWER'"
            + " AND subject_id IN (SELECT id FROM document WHERE message_id = ?)", messageId);

    List<String> categories = new ArrayList<>();
    for (JsonNode label : response.path("message_labels")) {
      String category = label.path("category").asText();
      categories.add(category);
      long classificationId = insertClassification(
          messageId, "MESSAGE", messageId, category,
          label.path("confidence").asDouble(),
          label.path("reason").asText(""),
          label.path("decided_by").asText("AI"),
          null);

      // E22: the ICSR element checklist behind a rule-decided label, stored so a reviewer can
      // check the decision element by element rather than taking the label on trust.
      if (category.startsWith("ICSR")) {
        storeIcsrValidity(classificationId, response, label.path("source_unit").asText(""));
      }
    }

    for (JsonNode unit : response.path("units")) {
      Long documentId = documentIdForUnit(messageId, unit.path("unit").asText());
      for (JsonNode label : unit.path("labels")) {
        insertClassification(messageId, "DOCUMENT", documentId != null ? documentId : messageId,
            label.path("category").asText(), label.path("confidence").asDouble(),
            label.path("reason").asText(""), label.path("decided_by").asText("AI"), documentId);
      }
      recordAiCall(job.id(), unit.path("ai_call"));
    }

    audit.systemForMessage("MESSAGE_CLASSIFIED", "INBOX_MESSAGE", messageId,
        "{\"categories\":" + categories.stream()
            .map(ParseDocumentHandler::jsonString).toList() + "}", messageId);

    log.info("Message {} classified as {}", messageId, categories);
    finish(messageId, categories);
  }

  // ---- units ------------------------------------------------------------------------------

  private List<Map<String, Object>> buildUnits(long messageId) {
    List<Map<String, Object>> units = new ArrayList<>();

    List<Map<String, Object>> documents = jdbc.queryForList(
        "SELECT id, source_kind, filename, parse_status, parse_error"
            + " FROM document WHERE message_id = ? ORDER BY id", messageId);

    for (Map<String, Object> document : documents) {
      long documentId = ((Number) document.get("ID")).longValue();
      String filename = (String) document.get("FILENAME");
      String status = (String) document.get("PARSE_STATUS");
      String name = "EMAIL_BODY".equals(document.get("SOURCE_KIND"))
          ? "email body" : filename;

      if (!"PARSED".equals(status)) {
        // E39: declared, not hidden. The classifier is told what it cannot see.
        units.add(Map.of(
            "name", name,
            "text", "[This attachment could not be read: "
                + nullIfBlank((String) document.get("PARSE_ERROR")) + "]",
            "pages", List.of()));
        continue;
      }

      // Selected as CLOBs and read with getString: TO_CHAR caps at 4,000 bytes and throws
      // ORA-22835 on any page longer than that, which is most of them (see ClobSupport).
      List<Map<String, Object>> pages = jdbc.query(
          "SELECT page_no, text_original, legibility, span_index_json"
              + " FROM document_page WHERE document_id = ? ORDER BY page_no",
          (rs, rowNum) -> {
            Map<String, Object> row = new HashMap<>();
            row.put("PAGE_NO", rs.getInt("page_no"));
            row.put("TEXT_ORIGINAL", ClobSupport.clob(rs, "text_original"));
            row.put("LEGIBILITY", rs.getDouble("legibility"));
            row.put("SPAN_INDEX_JSON", ClobSupport.clob(rs, "span_index_json"));
            return row;
          }, documentId);

      StringBuilder text = new StringBuilder();
      List<Map<String, Object>> pageInputs = new ArrayList<>();
      for (Map<String, Object> page : pages) {
        String pageText = (String) page.get("TEXT_ORIGINAL");
        if (pageText == null || pageText.isBlank()) {
          continue;
        }
        text.append(pageText).append("\n\n");
        Map<String, Object> pageInput = new HashMap<>();
        pageInput.put("document_id", documentId);
        pageInput.put("page_no", ((Number) page.get("PAGE_NO")).intValue());
        pageInput.put("text", pageText);
        pageInput.put("legibility", ((Number) page.get("LEGIBILITY")).doubleValue());
        pageInput.put("source_type",
            "EMAIL_BODY".equals(document.get("SOURCE_KIND")) ? "EMAIL_BODY" : "PDF_PAGE");
        pageInput.put("span_index", parseSpanIndex((String) page.get("SPAN_INDEX_JSON")));
        pageInputs.add(pageInput);
      }

      if (text.length() == 0) {
        continue;
      }
      units.add(Map.of("name", name, "text", text.toString().strip(), "pages", pageInputs));
    }

    return units;
  }

  private Object parseSpanIndex(String json) {
    if (json == null || json.isBlank()) {
      return List.of();
    }
    try {
      return new com.fasterxml.jackson.databind.ObjectMapper().readValue(json, List.class);
    } catch (Exception e) {
      return List.of();
    }
  }

  private String describeMessage(long messageId) {
    try {
      Map<String, Object> message = jdbc.queryForMap(
          "SELECT sender_email, subject FROM inbox_message WHERE id = ?", messageId);
      return String.format("Email from %s, subject: %s",
          message.get("SENDER_EMAIL"), message.get("SUBJECT"));
    } catch (RuntimeException e) {
      return "";
    }
  }

  private Long documentIdForUnit(long messageId, String unitName) {
    List<Long> ids = "email body".equals(unitName)
        ? jdbc.queryForList(
            "SELECT id FROM document WHERE message_id = ? AND source_kind = 'EMAIL_BODY'",
            Long.class, messageId)
        : jdbc.queryForList(
            "SELECT id FROM document WHERE message_id = ? AND filename = ?",
            Long.class, messageId, unitName);
    return ids.isEmpty() ? null : ids.get(0);
  }

  // ---- persistence --------------------------------------------------------------------------

  private long insertClassification(long messageId, String subjectType, long subjectId,
      String category, double confidence, String reason, String decidedBy, Long triggeredBy) {
    org.springframework.jdbc.support.KeyHolder keys =
        new org.springframework.jdbc.support.GeneratedKeyHolder();
    jdbc.update(connection -> {
      var ps = connection.prepareStatement(
          "INSERT INTO classification (subject_type, subject_id, category, confidence, reason,"
              + " triggered_by_document_id, decided_by, model, prompt_version)"
              + " VALUES (?,?,?,?,?,?,?,?,?)", new String[] {"id"});
      ps.setString(1, subjectType);
      ps.setLong(2, subjectId);
      ps.setString(3, category);
      ps.setDouble(4, Math.max(0, Math.min(1, confidence)));
      ps.setString(5, truncate(reason, 2000));
      if (triggeredBy == null) {
        ps.setNull(6, java.sql.Types.NUMERIC);
      } else {
        ps.setLong(6, triggeredBy);
      }
      ps.setString(7, decidedBy);
      ps.setString(8, "anthropic/claude-haiku-4.5");
      ps.setString(9, "P1_classify@v1");
      return ps;
    }, keys);
    return keys.getKey().longValue();
  }

  /**
   * Store the element checklist the label was actually decided from.
   *
   * <p>This used to read {@code units[0]} unconditionally. A message is classified unit by unit
   * and the message label is the strongest of them (E25), so on a covering email with a completed
   * form attached the label comes from the attachment while unit 0 is the email body. The result
   * was a card that said "All four ICSR minimum criteria are present" directly above a checklist
   * reading 1/4, with quotes from the wrong document — the screen contradicting itself on the one
   * claim a reviewer is being asked to check.
   *
   * <p>{@code source_unit} names the unit that produced the label. Falling back to the first unit
   * keeps older responses working, since they do not carry the field.
   */
  private void storeIcsrValidity(long classificationId, JsonNode response, String sourceUnit) {
    JsonNode elements = elementsForUnit(response, sourceUnit);
    if (elements.isMissingNode() || elements.isEmpty()) {
      return;
    }
    JsonNode patient = elements.path("has_identifiable_patient");
    JsonNode reporter = elements.path("has_identifiable_reporter");
    JsonNode product = elements.path("has_suspect_product");
    JsonNode event = elements.path("has_adverse_event");

    int present = 0;
    List<String> missing = new ArrayList<>();
    for (var entry : List.of(
        Map.entry("patient", patient), Map.entry("reporter", reporter),
        Map.entry("product", product), Map.entry("event", event))) {
      if (entry.getValue().path("present").asBoolean()) {
        present++;
      } else {
        missing.add(entry.getKey());
      }
    }

    jdbc.update(
        "INSERT INTO icsr_validity (classification_id, has_patient, patient_confidence,"
            + " patient_evidence, has_reporter, reporter_confidence, reporter_evidence,"
            + " has_product, product_confidence, product_evidence, has_event, event_confidence,"
            + " event_evidence, elements_present, missing_elements_json)"
            + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        classificationId,
        yn(patient), patient.path("confidence").asDouble(), truncate(patient.path("quote").asText(), 2000),
        yn(reporter), reporter.path("confidence").asDouble(), truncate(reporter.path("quote").asText(), 2000),
        yn(product), product.path("confidence").asDouble(), truncate(product.path("quote").asText(), 2000),
        yn(event), event.path("confidence").asDouble(), truncate(event.path("quote").asText(), 2000),
        present,
        missing.stream().map(ParseDocumentHandler::jsonString).toList().toString());
  }

  /** The {@code icsr_elements} of the named unit, or the first unit's if it cannot be found. */
  private static JsonNode elementsForUnit(JsonNode response, String sourceUnit) {
    if (sourceUnit != null && !sourceUnit.isBlank()) {
      for (JsonNode unit : response.path("units")) {
        if (sourceUnit.equals(unit.path("unit").asText())) {
          return unit.path("icsr_elements");
        }
      }
      log.warn("Classification names source unit '{}' but no such unit is in the response;"
          + " falling back to the first", sourceUnit);
    }
    return response.path("units").path(0).path("icsr_elements");
  }

  private static String yn(JsonNode node) {
    return node.path("present").asBoolean() ? "Y" : "N";
  }

  private void recordAiCall(long jobId, JsonNode call) {
    if (call.isMissingNode() || call.isEmpty()) {
      return;
    }
    jdbc.update(
        "INSERT INTO ai_call_log (job_id, purpose, model, prompt_version, request_json,"
            + " response_json, prompt_tokens, completion_tokens, cached_tokens,"
            + " cache_write_tokens, cost_usd, latency_ms, http_status, retries, repaired)"
            + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        jobId,
        truncate(call.path("purpose").asText("classify"), 60),
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

  // ---- next stage ---------------------------------------------------------------------------

  private void finish(long messageId, List<String> categories) {
    List<String> extractable = categories.stream()
        .filter(c -> !"NOT_RELEVANT".equals(c))
        .toList();

    if (extractable.isEmpty()) {
      // Nothing to extract. Straight to finalisation rather than burning calls on a newsletter.
      jdbc.update("UPDATE inbox_message SET status = 'CLASSIFIED' WHERE id = ?", messageId);
      queue.enqueue(JobType.FINALISE_MESSAGE, SubjectType.MESSAGE, messageId);
      return;
    }

    jdbc.update("UPDATE inbox_message SET status = 'EXTRACTING' WHERE id = ?", messageId);
    queue.enqueue(JobType.EXTRACT_CASE, SubjectType.MESSAGE, messageId, 5, 0,
        "{\"categories\":" + extractable.stream()
            .map(ParseDocumentHandler::jsonString).toList() + "}");
  }
}
