package com.clinevo.inbox.ingest;

import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.internet.MimeMessage;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Computes the key that makes polling idempotent (E2).
 *
 * <p>The poller runs every ten seconds. Without a stable identity per message, a restart, a
 * connection drop mid-flag, or a mailbox that fails to persist {@code \Seen} would re-ingest
 * the same case and create a duplicate — which in pharmacovigilance is a reportable data
 * quality problem, not an inconvenience.
 *
 * <p><b>Primary key: the {@code Message-ID} header.</b> It is supposed to be globally unique
 * and usually is. But it is optional in RFC 5322, some appliances strip it, and some bulk
 * senders reuse it, so a fallback is required rather than optional:
 *
 * <p><b>Fallback: SHA-256 of (from, subject, sent date, normalised body).</b> Normalising the
 * body — collapsing whitespace and lower-casing — means a mail gateway that re-wraps lines
 * does not manufacture a new identity for the same message.
 *
 * <p>The key is then subject to a UNIQUE constraint in Oracle, so idempotency is enforced by
 * the database rather than by remembering to check first.
 */
@Component
public class DedupeService {

  private static final Logger log = LoggerFactory.getLogger(DedupeService.class);
  private static final HexFormat HEX = HexFormat.of();

  /** The dedupe key and where it came from, so the audit trail can say which route was used. */
  public record DedupeKey(String value, String source, String messageIdHeader) {}

  public DedupeKey computeKey(Message message, String bodyText) {
    String messageId = headerValue(message, "Message-ID");
    if (messageId != null && !messageId.isBlank()) {
      String normalised = messageId.strip();
      // Oracle's column is 200 chars; a pathological Message-ID is hashed rather than truncated,
      // because truncation could collide two genuinely different messages.
      String key = normalised.length() <= 190
          ? normalised
          : "mid-sha:" + sha256(normalised);
      return new DedupeKey(key, "MESSAGE_ID", normalised);
    }

    log.debug("No Message-ID header; falling back to a content hash (E2)");
    String composite = String.join("",
        nullSafe(headerValue(message, "From")).toLowerCase(Locale.ROOT),
        nullSafe(subjectOf(message)).toLowerCase(Locale.ROOT),
        nullSafe(headerValue(message, "Date")),
        normaliseBody(bodyText));
    return new DedupeKey("hash:" + sha256(composite), "CONTENT_HASH", null);
  }

  /**
   * Collapses whitespace and case so that cosmetic re-wrapping by an intermediate mail server
   * does not change the identity of the message.
   */
  static String normaliseBody(String body) {
    if (body == null) {
      return "";
    }
    return body.replaceAll("\\s+", " ").strip().toLowerCase(Locale.ROOT);
  }

  private static String headerValue(Message message, String name) {
    try {
      String[] values = message.getHeader(name);
      return values == null || values.length == 0 ? null : values[0];
    } catch (MessagingException e) {
      return null;
    }
  }

  private static String subjectOf(Message message) {
    try {
      return message.getSubject();
    } catch (MessagingException e) {
      return null;
    }
  }

  private static String nullSafe(String value) {
    return value == null ? "" : value;
  }

  public static String sha256(String value) {
    try {
      return HEX.formatHex(
          MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException("SHA-256 unavailable", e);
    }
  }
}
