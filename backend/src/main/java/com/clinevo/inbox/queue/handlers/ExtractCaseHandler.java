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
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Extracts the fields for every matched category and stores them with their proven evidence.
 *
 * <p>The AI service has already done the work that matters: each field arrived with a quote, the
 * quote was searched for in the page it was attributed to, the offsets were rewritten to what
 * was actually found, a bounding box was resolved, and the confidence was put through the
 * deterministic chain. This handler's job is to persist that faithfully — including the
 * failures.
 *
 * <p>Unverified evidence is stored, not discarded. A citation the system could not prove is
 * exactly what a reviewer needs to see, and hiding it would defeat the entire point of
 * verifying (E27).
 */
@Component
public class ExtractCaseHandler implements JobHandler {

  private static final Logger log = LoggerFactory.getLogger(ExtractCaseHandler.class);
  private static final ObjectMapper MAPPER = new ObjectMapper();

  private final JdbcTemplate jdbc;
  private final AiServiceClient ai;
  private final JobQueueRepository queue;
  private final AuditService audit;

  public ExtractCaseHandler(JdbcTemplate jdbc, AiServiceClient ai,
      JobQueueRepository queue, AuditService audit) {
    this.jdbc = jdbc;
    this.ai = ai;
    this.queue = queue;
    this.audit = audit;
  }

  @Override
  public JobType type() {
    return JobType.EXTRACT_CASE;
  }

  @Override
  @Transactional
  public void handle(QueuedJob job) throws Exception {
    long messageId = job.subjectId();
    List<String> categories = readCategories(job.payloadJson());

    if (categories.isEmpty()) {
      log.warn("Extract job {} has no categories; nothing to do", job.id());
      queue.enqueue(JobType.FINALISE_MESSAGE, SubjectType.MESSAGE, messageId);
      return;
    }

    List<Map<String, Object>> units = buildUnits(messageId);
    if (units.isEmpty()) {
      log.warn("Message {} has no readable units to extract from", messageId);
      queue.enqueue(JobType.FINALISE_MESSAGE, SubjectType.MESSAGE, messageId);
      return;
    }

    JsonNode response = ai.extract(categories, units);

    // Idempotent re-run: drop this message's AI-derived cases and start again. Reviewer
    // overrides live in separate rows and are never touched (E40).
    jdbc.update(
        "DELETE FROM case_record WHERE message_id = ? AND id NOT IN"
            + " (SELECT case_id FROM extracted_field WHERE decided_by = 'REVIEWER')", messageId);

    long caseId = insertCase(messageId, response, categories);
    int stored = storeFields(caseId, response);

    for (JsonNode call : response.path("ai_calls")) {
      recordAiCall(job.id(), call);
    }

    JsonNode verification = response.path("verification");
    log.info("Message {}: {} field(s) stored, {}/{} verified ({}%), {} conflict(s)",
        messageId, stored,
        verification.path("verified_fields").asInt(),
        verification.path("asserted_fields").asInt(),
        Math.round(verification.path("verification_rate").asDouble() * 100),
        response.path("conflicts").size());

    audit.systemForMessage("CASE_EXTRACTED", "CASE_RECORD", caseId,
        String.format("{\"fields\":%d,\"verified\":%d,\"asserted\":%d,\"conflicts\":%d}",
            stored, verification.path("verified_fields").asInt(),
            verification.path("asserted_fields").asInt(), response.path("conflicts").size()),
        messageId);

    queue.enqueue(JobType.FINALISE_MESSAGE, SubjectType.MESSAGE, messageId);
  }

  // ---- input --------------------------------------------------------------------------------

  private List<String> readCategories(String payload) throws Exception {
    if (payload == null || payload.isBlank()) {
      return List.of();
    }
    JsonNode node = MAPPER.readTree(payload).path("categories");
    List<String> out = new ArrayList<>();
    node.forEach(n -> out.add(n.asText()));
    return out;
  }

  private List<Map<String, Object>> buildUnits(long messageId) {
    List<Map<String, Object>> units = new ArrayList<>();
    List<Map<String, Object>> documents = jdbc.queryForList(
        "SELECT id, source_kind, filename FROM document"
            + " WHERE message_id = ? AND parse_status = 'PARSED' ORDER BY id", messageId);

    for (Map<String, Object> document : documents) {
      long documentId = ((Number) document.get("ID")).longValue();
      boolean isBody = "EMAIL_BODY".equals(document.get("SOURCE_KIND"));
      String name = isBody ? "email body" : (String) document.get("FILENAME");

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
        int pageNo = ((Number) page.get("PAGE_NO")).intValue();
        String pageText = (String) page.get("TEXT_ORIGINAL");
        if (pageText == null || pageText.isBlank()) {
          continue;
        }
        // E15: sections marked excluded_from_case are withheld from extraction — a References
        // list names other authors' patients, and a case drawn from one is fabricated.
        String usable = withoutExcludedSections(documentId, pageNo, pageText);

        text.append(usable).append("\n\n");
        Map<String, Object> input = new HashMap<>();
        input.put("document_id", documentId);
        input.put("page_no", pageNo);
        // Verification runs against the FULL page text, not the redacted copy: a quote must be
        // provable against what the document actually says.
        input.put("text", pageText);
        input.put("legibility", ((Number) page.get("LEGIBILITY")).doubleValue());
        input.put("source_type", isBody ? "EMAIL_BODY" : "PDF_PAGE");
        input.put("span_index", parseSpanIndex((String) page.get("SPAN_INDEX_JSON")));
        pageInputs.add(input);
      }

      if (text.length() > 0) {
        units.add(Map.of("name", name, "text", text.toString().strip(), "pages", pageInputs));
      }
    }
    return units;
  }

  private String withoutExcludedSections(long documentId, int pageNo, String pageText) {
    List<Map<String, Object>> excluded = jdbc.queryForList(
        "SELECT heading, char_start, char_end FROM document_section"
            + " WHERE document_id = ? AND page_no = ? AND excluded_from_case = 'Y'"
            + " ORDER BY char_start", documentId, pageNo);
    if (excluded.isEmpty()) {
      return pageText;
    }

    StringBuilder out = new StringBuilder();
    int cursor = 0;
    for (Map<String, Object> section : excluded) {
      int start = Math.max(0, Math.min(((Number) section.get("CHAR_START")).intValue(),
          pageText.length()));
      int end = Math.max(start, Math.min(((Number) section.get("CHAR_END")).intValue(),
          pageText.length()));
      if (start > cursor) {
        out.append(pageText, cursor, start);
      }
      // Marked rather than silently cut, so the model can see that something was withheld and
      // does not treat the remainder as the whole document.
      out.append("\n[section '").append(section.get("HEADING"))
          .append("' withheld from case extraction]\n");
      cursor = Math.max(cursor, end);
    }
    if (cursor < pageText.length()) {
      out.append(pageText.substring(cursor));
    }
    return out.toString();
  }

  private Object parseSpanIndex(String json) {
    if (json == null || json.isBlank()) {
      return List.of();
    }
    try {
      return MAPPER.readValue(json, List.class);
    } catch (Exception e) {
      return List.of();
    }
  }

  // ---- persistence ---------------------------------------------------------------------------

  private long insertCase(long messageId, JsonNode response, List<String> categories) {
    JsonNode first = response.path("cases").path(0);
    String caseType = first.path("case_type").asText(
        categories.get(0).startsWith("ICSR") ? "ICSR" : categories.get(0));
    String narrative = first.path("narrative").asText("");
    double confidence = first.path("confidence").asDouble(0.0);

    boolean serious = false;
    List<String> criteria = new ArrayList<>();
    for (JsonNode field : response.path("fields")) {
      if (field.path("field_path").asText().endsWith(".seriousness")
          && "STATED".equals(field.path("status").asText())) {
        serious = true;
        for (JsonNode c : field.path("value_json").isTextual()
            ? MAPPER.createArrayNode() : field.path("value_json").path("criteria")) {
          criteria.add(c.asText());
        }
        if (criteria.isEmpty() && !field.path("value_text").asText("").isBlank()) {
          for (String c : field.path("value_text").asText().split(",\\s*")) {
            criteria.add(c);
          }
        }
      }
    }

    KeyHolder keys = new GeneratedKeyHolder();
    final boolean isSerious = serious;
    final String seriousnessJson =
        "{\"criteria\":" + criteria.stream().map(ParseDocumentHandler::jsonString).toList()
            + ",\"is_serious\":" + serious + "}";
    jdbc.update(connection -> {
      var ps = connection.prepareStatement(
          "INSERT INTO case_record (message_id, case_index, case_type, narrative, is_serious,"
              + " seriousness_json, confidence) VALUES (?,?,?,?,?,?,?)", new String[] {"id"});
      ps.setLong(1, messageId);
      ps.setInt(2, 0);
      ps.setString(3, caseType.startsWith("ICSR") ? "ICSR" : caseType);
      ps.setString(4, narrative);
      ps.setString(5, isSerious ? "Y" : "N");
      ps.setString(6, truncate(seriousnessJson, 2000));
      ps.setDouble(7, Math.max(0, Math.min(1, confidence)));
      return ps;
    }, keys);
    return keys.getKey().longValue();
  }

  private int storeFields(long caseId, JsonNode response) {
    int count = 0;
    for (JsonNode field : response.path("fields")) {
      KeyHolder keys = new GeneratedKeyHolder();
      jdbc.update(connection -> {
        var ps = connection.prepareStatement(
            "INSERT INTO extracted_field (case_id, field_group, field_path, field_index,"
                + " value_text, value_json, unit, raw_text, status, confidence,"
                + " confidence_pre_adjust, adjust_reason, decided_by)"
                + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'AI')", new String[] {"id"});
        ps.setLong(1, caseId);
        ps.setString(2, truncate(field.path("field_group").asText("OTHER"), 40));
        ps.setString(3, truncate(field.path("field_path").asText(), 200));
        ps.setInt(4, field.path("field_index").asInt());
        ps.setString(5, truncate(field.path("value_text").asText(""), 4000));
        ps.setString(6, nullIfBlank(field.path("value_json").asText(null)));
        ps.setString(7, truncate(nullIfBlank(field.path("unit").asText(null)), 40));
        ps.setString(8, truncate(nullIfBlank(field.path("raw_text").asText(null)), 2000));
        ps.setString(9, field.path("status").asText("NOT_STATED"));
        ps.setDouble(10, clamp(field.path("confidence").asDouble()));
        ps.setDouble(11, clamp(field.path("confidence_pre_adjust").asDouble()));
        ps.setString(12, truncate(field.path("adjust_reason").asText(""), 500));
        return ps;
      }, keys);

      long fieldId = keys.getKey().longValue();
      count++;

      for (JsonNode evidence : field.path("evidence")) {
        jdbc.update(
            "INSERT INTO field_evidence (field_id, source_type, document_id, page_no, quote,"
                + " char_start, char_end, bbox, verified, verify_method, match_score)"
                + " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            fieldId,
            evidence.path("source_type").asText("PDF_PAGE"),
            evidence.path("document_id").isNull() ? null : evidence.path("document_id").asLong(),
            evidence.path("page_no").isNull() ? null : evidence.path("page_no").asInt(),
            truncate(evidence.path("quote").asText(""), 4000),
            evidence.path("char_start").isNull() ? null : evidence.path("char_start").asInt(),
            evidence.path("char_end").isNull() ? null : evidence.path("char_end").asInt(),
            truncate(nullIfBlank(evidence.path("bbox").asText(null)), 200),
            evidence.path("verified").asText("N"),
            evidence.path("verify_method").asText("FAILED"),
            evidence.path("match_score").asDouble());
      }
    }
    return count;
  }

  private void recordAiCall(long jobId, JsonNode call) {
    jdbc.update(
        "INSERT INTO ai_call_log (job_id, purpose, model, prompt_version, request_json,"
            + " response_json, prompt_tokens, completion_tokens, cached_tokens,"
            + " cache_write_tokens, cost_usd, latency_ms, http_status, retries, repaired)"
            + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        jobId,
        truncate(call.path("purpose").asText("extract"), 60),
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

  private static double clamp(double value) {
    return Math.max(0.0, Math.min(1.0, value));
  }
}
