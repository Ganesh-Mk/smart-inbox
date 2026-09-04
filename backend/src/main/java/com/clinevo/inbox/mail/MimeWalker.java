package com.clinevo.inbox.mail;

import jakarta.mail.BodyPart;
import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.Multipart;
import jakarta.mail.Part;
import jakarta.mail.internet.MimeMessage;
import jakarta.mail.internet.MimeUtility;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.jsoup.Jsoup;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Flattens a MIME tree into "the body text" plus "the attachments".
 *
 * <p>Three shapes have to be handled and all three occur constantly in real safety mailboxes:
 *
 * <ul>
 *   <li><b>E3</b> — {@code multipart/alternative} (text + HTML), {@code multipart/related}
 *       (inline images), and arbitrarily nested {@code multipart/mixed}. The walk is
 *       depth-first; {@code text/plain} is preferred and the HTML is converted with jsoup and
 *       kept alongside rather than discarded.
 *   <li><b>E5</b> — a forwarded email arriving as a {@code message/rfc822} attachment, with
 *       the actual case one level down. Recurse exactly one level and hoist the inner
 *       attachments onto the parent, recording {@code nesting_level}. Deeper nesting is
 *       logged and skipped with a reason rather than followed indefinitely.
 *   <li><b>E4</b> — parts that lie about their type. The raw bytes are captured so
 *       {@link AttachmentSniffer} can decide.
 * </ul>
 */
@Component
public class MimeWalker {

  private static final Logger log = LoggerFactory.getLogger(MimeWalker.class);

  /** One extracted part, with everything the ingest layer needs to persist it. */
  public record RawAttachment(
      String filename,
      String declaredType,
      byte[] data,
      int nestingLevel,
      String skipReason) {

    public long size() {
      return data == null ? 0 : data.length;
    }
  }

  /** The flattened result of walking one message. */
  public record WalkResult(
      String textBody,
      String htmlBody,
      String charset,
      List<RawAttachment> attachments,
      boolean hadForwardedMessage,
      List<String> notes) {}

  private final int maxNestingLevel;

  public MimeWalker(com.clinevo.inbox.config.AppProperties props) {
    this.maxNestingLevel = props.mail().maxNestingLevel();
  }

  public WalkResult walk(Message message) {
    Accumulator acc = new Accumulator();
    try {
      collect(message, acc, 0);
    } catch (MessagingException | IOException e) {
      log.error("Failed to walk MIME tree", e);
      acc.notes.add("MIME walk failed: " + e.getMessage());
    }

    String text = acc.text.isBlank() && !acc.html.isBlank()
        ? htmlToText(acc.html)
        : acc.text.strip();

    return new WalkResult(
        text,
        acc.html.isBlank() ? null : acc.html,
        acc.charset,
        acc.attachments,
        acc.hadForwarded,
        acc.notes);
  }

  private static final class Accumulator {
    String text = "";
    String html = "";
    String charset = "UTF-8";
    final List<RawAttachment> attachments = new ArrayList<>();
    final List<String> notes = new ArrayList<>();
    boolean hadForwarded = false;
  }

  private void collect(Part part, Accumulator acc, int level)
      throws MessagingException, IOException {

    String contentType = safeContentType(part);
    String disposition = part.getDisposition();
    String filename = decodeFilename(part.getFileName());

    // --- E5: a forwarded message carried as an attachment ---
    if (contentType.startsWith("message/rfc822")) {
      acc.hadForwarded = true;
      if (level >= maxNestingLevel) {
        acc.notes.add("Nested message at level " + (level + 1) + " skipped: exceeds max depth "
            + maxNestingLevel);
        acc.attachments.add(new RawAttachment(
            filename != null ? filename : "forwarded-message.eml",
            "message/rfc822", readBytes(part), level + 1, "NESTING_TOO_DEEP"));
        return;
      }
      Object content = part.getContent();
      if (content instanceof Message inner) {
        // The inner body is prepended context, not the primary source: the outer message may
        // say nothing at all ("passing this on"), and the case lives one level down.
        Accumulator innerAcc = new Accumulator();
        collect(inner, innerAcc, level + 1);
        acc.text = joinBodies(acc.text, innerAcc.text, inner);
        if (acc.html.isBlank()) {
          acc.html = innerAcc.html;
        }
        // Hoist the inner attachments onto the parent, marked with their real nesting level.
        for (RawAttachment attachment : innerAcc.attachments) {
          acc.attachments.add(new RawAttachment(
              attachment.filename(), attachment.declaredType(), attachment.data(),
              Math.max(attachment.nestingLevel(), level + 1), attachment.skipReason()));
        }
        acc.notes.addAll(innerAcc.notes);
        acc.notes.add("Recursed one level into a forwarded message/rfc822 part (E5)");
      }
      return;
    }

    // --- multipart: depth-first ---
    if (part.isMimeType("multipart/*")) {
      Object content = part.getContent();
      if (content instanceof Multipart multipart) {
        boolean alternative = part.isMimeType("multipart/alternative");
        for (int i = 0; i < multipart.getCount(); i++) {
          BodyPart child = multipart.getBodyPart(i);
          collect(child, acc, level);
        }
        if (alternative && !acc.text.isBlank() && !acc.html.isBlank()) {
          acc.notes.add("multipart/alternative: kept text/plain as primary, HTML alongside (E3)");
        }
      }
      return;
    }

    boolean isAttachment = Part.ATTACHMENT.equalsIgnoreCase(disposition)
        || (filename != null && !filename.isBlank());

    // --- body parts ---
    if (!isAttachment && part.isMimeType("text/plain")) {
      String value = asString(part);
      acc.text = acc.text.isBlank() ? value : acc.text + "\n\n" + value;
      String cs = charsetOf(contentType);
      if (cs != null) {
        acc.charset = cs;
      }
      return;
    }
    if (!isAttachment && part.isMimeType("text/html")) {
      String value = asString(part);
      acc.html = acc.html.isBlank() ? value : acc.html + "\n" + value;
      return;
    }

    // --- everything else is an attachment, including inline images ---
    byte[] data = readBytes(part);
    acc.attachments.add(new RawAttachment(
        filename != null ? filename : defaultName(contentType, acc.attachments.size()),
        stripParameters(contentType),
        data,
        level,
        null));
  }

  private static String joinBodies(String outer, String inner, Message innerMessage) {
    String header;
    try {
      header = String.format(
          "%n%n----- Forwarded message -----%nFrom: %s%nSubject: %s%n%n",
          firstAddress(innerMessage), innerMessage.getSubject());
    } catch (MessagingException e) {
      header = "\n\n----- Forwarded message -----\n\n";
    }
    if (outer.isBlank()) {
      return inner;
    }
    return outer.stripTrailing() + header + inner;
  }

  private static String firstAddress(Message message) {
    try {
      var from = message.getFrom();
      return from != null && from.length > 0 ? from[0].toString() : "unknown";
    } catch (MessagingException e) {
      return "unknown";
    }
  }

  /** HTML to readable text, preserving paragraph and line breaks (E3). */
  public String htmlToText(String html) {
    if (html == null || html.isBlank()) {
      return "";
    }
    org.jsoup.nodes.Document document = Jsoup.parse(html);
    document.outputSettings().prettyPrint(false);
    document.select("br").append("\\n");
    document.select("p, div, tr, li, h1, h2, h3, h4").prepend("\\n");
    String text = document.text().replace("\\n", "\n");
    return text.replaceAll("\n{3,}", "\n\n").strip();
  }

  private static String asString(Part part) throws MessagingException, IOException {
    Object content = part.getContent();
    if (content instanceof String s) {
      return s;
    }
    if (content instanceof InputStream in) {
      return new String(in.readAllBytes(), StandardCharsets.UTF_8);
    }
    return String.valueOf(content);
  }

  private static byte[] readBytes(Part part) throws MessagingException, IOException {
    try (InputStream in = part.getInputStream();
         ByteArrayOutputStream out = new ByteArrayOutputStream()) {
      in.transferTo(out);
      return out.toByteArray();
    } catch (IOException e) {
      // message/rfc822 parts sometimes refuse getInputStream(); fall back to writeTo.
      try (ByteArrayOutputStream out = new ByteArrayOutputStream()) {
        part.writeTo(out);
        return out.toByteArray();
      }
    }
  }

  private static String safeContentType(Part part) {
    try {
      String type = part.getContentType();
      return type == null ? "application/octet-stream" : type.toLowerCase(java.util.Locale.ROOT);
    } catch (MessagingException e) {
      return "application/octet-stream";
    }
  }

  private static String stripParameters(String contentType) {
    int semicolon = contentType.indexOf(';');
    return (semicolon < 0 ? contentType : contentType.substring(0, semicolon)).strip();
  }

  private static String charsetOf(String contentType) {
    int index = contentType.indexOf("charset=");
    if (index < 0) {
      return null;
    }
    String value = contentType.substring(index + 8).strip();
    if (value.startsWith("\"")) {
      value = value.substring(1);
    }
    int end = value.indexOf('"');
    if (end > 0) {
      value = value.substring(0, end);
    }
    end = value.indexOf(';');
    if (end > 0) {
      value = value.substring(0, end);
    }
    return value.strip();
  }

  /** RFC 2047 / RFC 2231 encoded filenames — the Japanese attachment needs this. */
  private static String decodeFilename(String raw) {
    if (raw == null) {
      return null;
    }
    try {
      return MimeUtility.decodeText(raw);
    } catch (Exception e) {
      return raw;
    }
  }

  private static String defaultName(String contentType, int index) {
    String subtype = stripParameters(contentType);
    int slash = subtype.indexOf('/');
    String extension = slash > 0 ? subtype.substring(slash + 1) : "bin";
    return "part-" + (index + 1) + "." + extension;
  }
}
