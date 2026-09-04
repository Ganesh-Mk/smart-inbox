package com.clinevo.inbox.mail;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

/**
 * Finds where a reply's new text ends and the quoted history begins (E10).
 *
 * <p>Why this matters: a safety mailbox is full of reply chains in which the original case is
 * quoted in full underneath a one-line follow-up. Extracting from the whole body counts the
 * same case twice — once as a new report and once as history — which in a regulated system is
 * a duplicate case, not a cosmetic problem.
 *
 * <p>The quoted region is <em>kept</em>, not deleted. Sometimes it is the only place a fact
 * appears: a message that says only "passing this to safety" above a quoted enquiry has all
 * its content below the boundary. So the rule is de-prioritisation, not removal — new text is
 * the primary source, quoted text is secondary and never counts as an independent report.
 */
@Component
public class QuotedTextDetector {

  // Note "[ \t]*" rather than "\s*" immediately after every line anchor. In multiline mode
  // "\s" matches a newline too, so "^\s*" happily begins the match on the blank line *before*
  // the marker. The reported boundary then points at whitespace instead of at the marker, and
  // the quoted region comes back with a stray leading newline.

  /** Attribution lines: "On <date>, <person> wrote:" in its many variations. */
  private static final Pattern ON_WROTE = Pattern.compile(
      "(?m)^[ \\t]*On\\s+.{4,120}?\\s+wrote:[ \\t]*$");

  /** Outlook's separator, localised variants included. */
  private static final Pattern ORIGINAL_MESSAGE = Pattern.compile(
      "(?mi)^[ \\t]*-{2,}[ \\t]*(Original Message|Forwarded message|Ursprüngliche Nachricht|"
          + "Message d'origine|Mensaje original)[ \\t]*-{2,}[ \\t]*$");

  /** A header block pasted inline by a forwarding client. */
  private static final Pattern FORWARD_HEADER_BLOCK = Pattern.compile(
      "(?mi)^[ \\t]*(From|Von|De|差出人):[ \\t]*.+$\\n^[ \\t]*(Sent|Date|Gesendet|Envoyé|送信日時):[ \\t]*.+$");

  /** Three or more consecutive lines starting with '>' — a quoted block by convention. */
  private static final Pattern QUOTE_PREFIX_RUN = Pattern.compile(
      "(?m)(^>.*$\\n?){3,}");

  /** Signature separator; not a quote boundary, but the same "stop reading here" idea. */
  private static final Pattern SIGNATURE = Pattern.compile("(?m)^--\\s*$");

  /**
   * The character offset in {@code body} at which quoted history begins, or {@code -1} when
   * the message has none.
   *
   * <p>The earliest matching marker wins: a reply that carries both an attribution line and a
   * run of '>' prefixes should split at whichever comes first.
   */
  public int findQuoteBoundary(String body) {
    if (body == null || body.isBlank()) {
      return -1;
    }
    int earliest = Integer.MAX_VALUE;
    for (Pattern pattern : List.of(
        ON_WROTE, ORIGINAL_MESSAGE, FORWARD_HEADER_BLOCK, QUOTE_PREFIX_RUN)) {
      Matcher matcher = pattern.matcher(body);
      if (matcher.find()) {
        earliest = Math.min(earliest, matcher.start());
      }
    }
    if (earliest == Integer.MAX_VALUE) {
      return -1;
    }
    // A marker in the first few characters means the whole message is quoted history — a bare
    // forward with no new text. Report offset 0 rather than pretending there is new text.
    return earliest;
  }

  /** The new text: everything above the boundary, or the whole body when there is none. */
  public String newText(String body) {
    int boundary = findQuoteBoundary(body);
    return boundary < 0 ? body : body.substring(0, boundary).stripTrailing();
  }

  /** The quoted history, or an empty string. Retained for the prompt and for the UI. */
  public String quotedText(String body) {
    int boundary = findQuoteBoundary(body);
    return boundary < 0 ? "" : body.substring(boundary);
  }

  /**
   * True when the message is essentially all quoted history — a bare forward.
   *
   * <p>These need the quoted region read after all, so the caller must not simply drop it.
   */
  public boolean isMostlyQuoted(String body) {
    int boundary = findQuoteBoundary(body);
    if (boundary < 0) {
      return false;
    }
    String newText = body.substring(0, boundary).strip();
    return newText.length() < 40;
  }

  /** Strips a trailing "-- \n signature" block from the new text, for cleaner prompts. */
  public String withoutSignature(String text) {
    Matcher matcher = SIGNATURE.matcher(text);
    return matcher.find() ? text.substring(0, matcher.start()).stripTrailing() : text;
  }
}
