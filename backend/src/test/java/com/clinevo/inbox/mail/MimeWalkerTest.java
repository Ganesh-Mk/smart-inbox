package com.clinevo.inbox.mail;

import static org.assertj.core.api.Assertions.assertThat;

import com.clinevo.inbox.config.AppProperties;
import jakarta.mail.Session;
import jakarta.mail.internet.MimeMessage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Walks the *actual committed corpus files*, not hand-built fixtures.
 *
 * <p>That matters: a fixture written in the test proves the walker handles the MIME the test
 * author imagined. These are the same bytes GreenMail serves and the same bytes a reviewer can
 * open in a text editor.
 */
class MimeWalkerTest {

  private static final Path EMAILS =
      Path.of("..", "testdata", "corpus", "emails").toAbsolutePath().normalize();

  private final MimeWalker walker = new MimeWalker(testProperties(1));

  private static AppProperties testProperties(int maxNesting) {
    return new AppProperties(
        new AppProperties.Mail("localhost", 3143, "safety@smart-inbox.test", "",
            false, "INBOX", 10000, false, maxNesting),
        new AppProperties.Limits(25, 60),
        new AppProperties.Queue(0, 2, 1000, 300, 60000, 3),
        new AppProperties.Ai("http://localhost:8000", 300),
        new AppProperties.Storage("./data/blobs", "./data/renders"),
        new AppProperties.Security("reviewer", "reviewer", "admin", "admin"));
  }

  private MimeMessage load(String key) throws Exception {
    Path path = EMAILS.resolve(key + ".eml");
    assertThat(path).as("corpus file must exist — run: python -m testdata.generator.build")
        .exists();
    try (InputStream in = Files.newInputStream(path)) {
      return new MimeMessage(Session.getInstance(new Properties()), in);
    }
  }

  @Test
  @DisplayName("E3: multipart/alternative keeps text/plain as primary and HTML alongside")
  void multipartAlternativePrefersPlainText() throws Exception {
    MimeWalker.WalkResult result = walker.walk(load("icsr-07-multi-product"));

    // The body carries the clinical description; "angioedema" is the coded term and appears
    // only in the subject line, so asserting on it here would test the wrong string.
    assertThat(result.textBody())
        .contains("swelling of the lips and tongue", "Pralextin", "Astelvia");
    assertThat(result.htmlBody()).isNotNull().contains("<p");
    // The plain part must win — it is the one with reliable offsets for evidence.
    assertThat(result.textBody()).doesNotContain("<p>", "<html");
  }

  @Test
  @DisplayName("E5: recursing one level into message/rfc822 finds the case and its attachment")
  void recursesOneLevelIntoForwardedMessage() throws Exception {
    MimeWalker.WalkResult result = walker.walk(load("adv-02-forwarded-rfc822"));

    assertThat(result.hadForwardedMessage()).isTrue();

    // The outer body says nothing clinical; classifying it alone would give NOT_RELEVANT.
    assertThat(result.textBody()).contains("came into the general enquiries inbox");
    // The real case, one level down, must now be present.
    assertThat(result.textBody()).contains("Fenaquil", "jaundice");
    assertThat(result.textBody()).contains("----- Forwarded message -----");

    // The inner PDF is hoisted onto the parent, marked with its true nesting level.
    assertThat(result.attachments())
        .filteredOn(a -> a.filename().endsWith(".pdf"))
        .singleElement()
        .satisfies(a -> {
          assertThat(a.filename()).isEqualTo("AER-2026-00188.pdf");
          assertThat(a.nestingLevel()).isEqualTo(1);
          assertThat(a.skipReason()).isNull();
        });
  }

  @Test
  @DisplayName("E5: nesting deeper than the configured limit is skipped with a reason, not followed")
  void refusesToRecurseBeyondTheLimit() throws Exception {
    MimeWalker shallow = new MimeWalker(testProperties(0));

    MimeWalker.WalkResult result = shallow.walk(load("adv-02-forwarded-rfc822"));

    assertThat(result.hadForwardedMessage()).isTrue();
    assertThat(result.attachments())
        .filteredOn(a -> "NESTING_TOO_DEEP".equals(a.skipReason()))
        .isNotEmpty();
    // The inner case is NOT silently pulled in when we said not to recurse.
    assertThat(result.textBody()).doesNotContain("jaundice");
    assertThat(result.notes()).anyMatch(n -> n.contains("exceeds max depth"));
  }

  @Test
  @DisplayName("E4: the declared content type is preserved verbatim, wrong or not")
  void preservesDeclaredTypeForAudit() throws Exception {
    MimeWalker.WalkResult result = walker.walk(load("adv-05-mislabelled-octet-stream"));

    assertThat(result.attachments()).singleElement().satisfies(a -> {
      assertThat(a.filename()).isEqualTo("report_export.dat");
      // The walker records the lie; the sniffer is what catches it.
      assertThat(a.declaredType()).isEqualTo("application/octet-stream");
      assertThat(a.data()).startsWith("%PDF-".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
    });
  }

  @Test
  @DisplayName("E9: the same PDF under two filenames yields two parts with identical bytes")
  void duplicateAttachmentsBothArrive() throws Exception {
    MimeWalker.WalkResult result = walker.walk(load("adv-01-duplicate-pdf"));

    assertThat(result.attachments()).hasSize(2);
    assertThat(result.attachments().get(0).filename())
        .isNotEqualTo(result.attachments().get(1).filename());
    // Identical content: it is the blob store's job to notice, not the walker's.
    assertThat(result.attachments().get(0).data())
        .isEqualTo(result.attachments().get(1).data());
  }

  @Test
  @DisplayName("a non-ASCII attachment filename is decoded, not left as =?utf-8?B?...")
  void decodesEncodedFilenames() throws Exception {
    MimeWalker.WalkResult result = walker.walk(load("lang-03-japanese"));

    assertThat(result.attachments()).singleElement().satisfies(a ->
        assertThat(a.filename()).isEqualTo("副作用報告書.pdf"));
    assertThat(result.textBody()).contains("医薬品副作用報告書");
  }

  @Test
  @DisplayName("both unsupported attachments are surfaced for the ingest layer to log")
  void surfacesUnsupportedAttachments() throws Exception {
    MimeWalker.WalkResult result = walker.walk(load("adv-04-unsupported-types"));

    assertThat(result.attachments())
        .extracting(MimeWalker.RawAttachment::filename)
        .containsExactlyInAnyOrder("reporting_guidance.docx", "case_bundle.zip");
  }

  @Test
  @DisplayName("HTML is converted to readable text when there is no plain part")
  void convertsHtmlWhenPlainIsAbsent() {
    String text = walker.htmlToText(
        "<html><body><p>First paragraph.</p><p>Second paragraph.</p>"
            + "<div>Third<br>with a break</div></body></html>");

    assertThat(text).contains("First paragraph.", "Second paragraph.", "Third", "with a break");
    assertThat(text).doesNotContain("<p>", "<div>", "<br>");
  }
}
