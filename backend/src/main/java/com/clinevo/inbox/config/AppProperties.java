package com.clinevo.inbox.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Every tunable the application has, bound from the {@code inbox.*} tree in application.yml.
 *
 * <p>Records with constructor binding, so the configuration is immutable and a typo in a
 * property name fails at startup rather than silently leaving a default in place.
 */
@ConfigurationProperties(prefix = "inbox")
public record AppProperties(
    Mail mail,
    Limits limits,
    Queue queue,
    Ai ai,
    Storage storage,
    Security security) {

  /** Mailbox connection. Four values repoint this at Gmail instead of GreenMail (E1). */
  public record Mail(
      @DefaultValue("localhost") String host,
      @DefaultValue("3143") int port,
      @DefaultValue("safety@smart-inbox.test") String user,
      @DefaultValue("") String password,
      @DefaultValue("false") boolean ssl,
      @DefaultValue("INBOX") String folder,
      @DefaultValue("10000") long pollIntervalMs,
      @DefaultValue("true") boolean pollEnabled,
      /** E5: recurse exactly one level into a message/rfc822 forward. */
      @DefaultValue("1") int maxNestingLevel) {}

  /** E8: caps that stop a 60 MB colour scan taking the service down on an 8 GB machine. */
  public record Limits(
      @DefaultValue("25") int maxAttachmentMb,
      @DefaultValue("60") int maxPdfPages) {

    public long maxAttachmentBytes() {
      return (long) maxAttachmentMb * 1024 * 1024;
    }
  }

  /** The durable work queue (R18, E37, E38). */
  public record Queue(
      @DefaultValue("4") int workerThreads,
      @DefaultValue("2") int batchSize,
      @DefaultValue("1000") long pollIntervalMs,
      /** A job locked longer than this is presumed abandoned by a crashed worker. */
      @DefaultValue("300") int leaseSeconds,
      @DefaultValue("60000") long reapIntervalMs,
      @DefaultValue("3") int maxAttempts) {}

  /** The Python AI service. Spring never calls an LLM directly (PROJECT_PLAN §5.1). */
  public record Ai(
      @DefaultValue("http://localhost:8000") String baseUrl,
      @DefaultValue("300") int timeoutSeconds) {

    public Duration timeout() {
      return Duration.ofSeconds(timeoutSeconds);
    }
  }

  /** E9: content-addressed blob store, so a duplicate attachment costs nothing. */
  public record Storage(
      @DefaultValue("./data/blobs") String blobDir,
      @DefaultValue("./data/renders") String renderDir) {}

  /**
   * HTTP Basic with two in-memory roles — enough to make the reviewer identity real for the
   * audit trail. Declared as a deliberate prototype simplification in the write-up (§8.4).
   */
  public record Security(
      @DefaultValue("reviewer") String reviewerUser,
      @DefaultValue("reviewer") String reviewerPassword,
      @DefaultValue("admin") String adminUser,
      @DefaultValue("admin") String adminPassword) {}
}
