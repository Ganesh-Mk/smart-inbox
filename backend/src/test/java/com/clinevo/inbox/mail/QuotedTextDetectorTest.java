package com.clinevo.inbox.mail;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.mail.Session;
import jakarta.mail.internet.MimeMessage;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * E10: reply chains.
 *
 * <p>The two corpus messages here are deliberately opposite cases, and getting both right is
 * the whole point:
 *
 * <ul>
 *   <li>{@code adv-08} — the quoted block <em>repeats</em> a case already reported. Extracting
 *       from it would create a duplicate case.
 *   <li>{@code adv-09} — the quoted block is the <em>only</em> place the enquiry appears.
 *       Deleting it would lose the message entirely.
 * </ul>
 *
 * <p>So the rule cannot be "strip quoted text". It is: split, prefer the new text, keep the
 * rest.
 */
class QuotedTextDetectorTest {

  private static final Path EMAILS =
      Path.of("..", "testdata", "corpus", "emails").toAbsolutePath().normalize();

  private final QuotedTextDetector detector = new QuotedTextDetector();

  private String bodyOf(String key) throws Exception {
    try (InputStream in = Files.newInputStream(EMAILS.resolve(key + ".eml"))) {
      MimeMessage message = new MimeMessage(Session.getInstance(new Properties()), in);
      return message.getContent().toString();
    }
  }

  @Test
  @DisplayName("'On <date>, X wrote:' splits follow-up from the repeated case")
  void detectsOnWroteBoundary() throws Exception {
    String body = bodyOf("adv-08-quoted-reply-chain");

    int boundary = detector.findQuoteBoundary(body);
    assertThat(boundary).isGreaterThan(0);

    String newText = detector.newText(body);
    String quoted = detector.quotedText(body);

    // The follow-up is the primary source.
    assertThat(newText).contains("rash has now completely resolved");
    // The original case is present but below the boundary, so it is never counted as a
    // second independent report.
    assertThat(newText).doesNotContain("began Velmoradine 20 mg once daily for essential");
    assertThat(quoted).contains("Velmoradine", "maculopapular");
    assertThat(detector.isMostlyQuoted(body)).isFalse();
  }

  @Test
  @DisplayName("'-----Original Message-----' is detected, and the quoted text is kept")
  void detectsOriginalMessageBoundary() throws Exception {
    String body = bodyOf("adv-09-original-message-chain");

    int boundary = detector.findQuoteBoundary(body);
    assertThat(boundary).isGreaterThan(0);

    String newText = detector.newText(body);
    String quoted = detector.quotedText(body);

    assertThat(newText).contains("Passing this to the safety mailbox");
    // The enquiry itself lives only in the quoted block. Discarding quoted text would throw
    // away the only content this message has.
    assertThat(quoted).contains("left the pack in a car overnight");
    assertThat(newText).doesNotContain("left the pack in a car overnight");
  }

  @Test
  @DisplayName("a message with no quoting reports no boundary and is unchanged")
  void plainMessageHasNoBoundary() throws Exception {
    String body = bodyOf("icsr-01-complete-body");

    assertThat(detector.findQuoteBoundary(body)).isEqualTo(-1);
    assertThat(detector.newText(body)).isEqualTo(body);
    assertThat(detector.quotedText(body)).isEmpty();
  }

  @Test
  @DisplayName("a run of '>' prefixed lines is a boundary even with no attribution line")
  void detectsBarePrefixRun() {
    String body = """
        Please see below.

        > A 58-year-old female developed a rash.
        > The drug was withdrawn.
        > She has since recovered.
        """;

    assertThat(detector.findQuoteBoundary(body)).isGreaterThan(0);
    assertThat(detector.newText(body).strip()).isEqualTo("Please see below.");
  }

  @Test
  @DisplayName("a bare forward with almost no new text is flagged as mostly quoted")
  void recognisesBareForward() {
    String body = """
        FYI

        -----Original Message-----
        From: someone@example.test
        Sent: 01 January 2026 09:00
        To: safety@smart-inbox.test
        Subject: A case

        A 58-year-old female developed a rash after starting Velmoradine.
        """;

    assertThat(detector.isMostlyQuoted(body))
        .as("the quoted region is the only real content, so it must not be de-prioritised away")
        .isTrue();
  }

  @Test
  @DisplayName("the earliest marker wins when a message carries several")
  void earliestMarkerWins() {
    String body = """
        My update here.

        On Mon, 01 Jan 2026 at 09:00, Dr X <x@example.test> wrote:
        > the original case
        > continues here
        > and here
        """;

    int boundary = detector.findQuoteBoundary(body);
    assertThat(body.substring(boundary)).startsWith("On Mon");
  }
}
