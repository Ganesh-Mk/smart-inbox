package com.clinevo.inbox.ai;

import com.clinevo.inbox.config.AppProperties;
import com.fasterxml.jackson.databind.JsonNode;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.MediaType;
import org.springframework.http.client.MultipartBodyBuilder;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.BodyInserters;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

/**
 * The only route from Java to any AI capability.
 *
 * <p>Spring Boot never calls an LLM itself. It owns state, orchestration and the audit trail;
 * the Python service is a stateless pure function from bytes and a task to JSON
 * (PROJECT_PLAN §5.1). Keeping that boundary sharp is what makes the AI side independently
 * testable and the Java side free of any model-specific code.
 *
 * <p>Responses are returned as {@link JsonNode} rather than mapped to a wall of records. The
 * envelope is large, deeply optional and evolves with the pipeline; a typed mirror of it would
 * be a second schema to keep in step with the pydantic models that already define the contract.
 * The handlers read the handful of paths they need.
 */
@Component
public class AiServiceClient {

  private static final Logger log = LoggerFactory.getLogger(AiServiceClient.class);

  private final WebClient webClient;
  private final Duration timeout;

  public AiServiceClient(AppProperties props, WebClient.Builder builder) {
    this.timeout = props.ai().timeout();
    this.webClient = builder
        .baseUrl(props.ai().baseUrl())
        // Parse envelopes carry full page text plus a span index; the default 256 KB buffer is
        // far too small and fails with an opaque DataBufferLimitException.
        .codecs(c -> c.defaultCodecs().maxInMemorySize(64 * 1024 * 1024))
        .build();
  }

  /** True when the AI service answers its health probe. */
  public boolean isHealthy() {
    try {
      JsonNode body = webClient.get().uri("/health")
          .retrieve().bodyToMono(JsonNode.class)
          .timeout(Duration.ofSeconds(5)).block();
      return body != null && "UP".equals(body.path("status").asText());
    } catch (RuntimeException e) {
      return false;
    }
  }

  /** Parse a PDF. The service caches by content hash, so a repeat costs nothing (E9). */
  public JsonNode parse(byte[] content, String filename, boolean useVision) {
    MultipartBodyBuilder body = new MultipartBodyBuilder();
    body.part("file", new ByteArrayResource(content) {
      @Override
      public String getFilename() {
        return filename == null ? "document.pdf" : filename;
      }
    }).contentType(MediaType.APPLICATION_PDF);
    body.part("use_vision", String.valueOf(useVision));

    return post("/v1/parse", BodyInserters.fromMultipartData(body.build()), "parse " + filename);
  }

  /** Parse an email body. No model call — the text is already text (E11). */
  public JsonNode parseEmailBody(String text, String html) {
    return post("/v1/parse/email-body",
        Map.of("text", text == null ? "" : text, "html", html == null ? "" : html),
        "parse email body");
  }

  /** Classify each unit and roll up to the message (E25). */
  public JsonNode classify(List<Map<String, Object>> units, String sourceDescription) {
    return post("/v1/classify",
        Map.of("units", units, "source_description", sourceDescription),
        "classify");
  }

  /** Extract fields for the matched categories, verify every quote, merge across units. */
  public JsonNode extract(List<String> categories, List<Map<String, Object>> units) {
    return post("/v1/extract",
        Map.of("categories", categories, "units", units),
        "extract");
  }

  /** R7: 10-15 sentence summary plus a relevance verdict. */
  public JsonNode summarise(String text) {
    return post("/v1/summarise", Map.of("text", text), "summarise");
  }

  /** Bonus: screen one article for individual reportable cases (E32). */
  public JsonNode screenArticle(byte[] content, String filename) {
    MultipartBodyBuilder body = new MultipartBodyBuilder();
    body.part("file", new ByteArrayResource(content) {
      @Override
      public String getFilename() {
        return filename == null ? "article.pdf" : filename;
      }
    }).contentType(MediaType.APPLICATION_PDF);

    return post("/v1/literature/screen",
        BodyInserters.fromMultipartData(body.build()), "screen " + filename);
  }

  /**
   * One request path for both shapes we send: multipart (file uploads) and JSON.
   *
   * <p>Kept as a single method taking `Object` rather than typed overloads, because
   * `BodyInserters` returns unrelated interfaces for the two cases and overload resolution
   * between them is ambiguous.
   */
  private JsonNode post(String path, Object body, String what) {
    try {
      WebClient.RequestBodySpec request = webClient.post().uri(path);
      WebClient.RequestHeadersSpec<?> prepared;

      if (body instanceof BodyInserters.MultipartInserter multipart) {
        prepared = request.body(multipart);
      } else {
        prepared = request.contentType(MediaType.APPLICATION_JSON).bodyValue(body);
      }

      JsonNode response = prepared.retrieve()
          .bodyToMono(JsonNode.class)
          .timeout(timeout)
          .block();

      if (response == null) {
        throw new AiServiceException(what + ": AI service returned an empty body");
      }
      return response;
    } catch (WebClientResponseException e) {
      // The service reports a real reason in `detail`; surfacing it beats a bare status code
      // when the failure lands in JOB.last_error for someone to read later.
      String detail = e.getResponseBodyAsString();
      log.error("AI service {} failed with {}: {}", what, e.getStatusCode(), detail);
      throw new AiServiceException(
          what + ": AI service returned " + e.getStatusCode() + " " + truncate(detail), e);
    } catch (AiServiceException e) {
      throw e;
    } catch (RuntimeException e) {
      throw new AiServiceException(
          what + ": " + e.getClass().getSimpleName() + " " + e.getMessage(), e);
    }
  }

  private static String truncate(String value) {
    if (value == null) {
      return "";
    }
    return value.length() <= 500 ? value : value.substring(0, 500) + "…";
  }

  /** Failure talking to the AI service. Retried with backoff by the queue (E36). */
  public static class AiServiceException extends RuntimeException {
    public AiServiceException(String message) {
      super(message);
    }

    public AiServiceException(String message, Throwable cause) {
      super(message, cause);
    }
  }
}
