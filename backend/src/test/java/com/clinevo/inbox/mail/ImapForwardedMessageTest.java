package com.clinevo.inbox.mail;

import static org.assertj.core.api.Assertions.assertThat;

import com.clinevo.inbox.config.AppProperties;
import com.icegreen.greenmail.junit5.GreenMailExtension;
import com.icegreen.greenmail.util.ServerSetup;
import com.icegreen.greenmail.util.ServerSetupTest;
import jakarta.mail.Message;
import jakarta.mail.Session;
import jakarta.mail.internet.MimeMessage;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;

/**
 * Regression test for a bug that a file-based fixture cannot catch.
 *
 * <p>Reading an {@code .eml} from disk gives a fully materialised message, so
 * {@link MimeWalkerTest} walks nested {@code message/rfc822} parts perfectly and passes. Over a
 * real IMAP connection the same code silently produced a <b>zero-byte</b> attachment with no
 * filename: jakarta.mail's {@code IMAPMessage} fetches parts lazily, and the nested message's
 * content is not reliably readable once the folder's stream has moved on.
 *
 * <p>The failure mode is the dangerous kind — no exception, no warning. The forwarded PDF was
 * recorded as {@code skip_reason='EMPTY'} and the case inside it simply vanished from the
 * queue. It was found by running the application against the GreenMail container and comparing
 * the resulting rows against the corpus manifest, not by a test.
 *
 * <p>So this test uses a real in-process IMAP server, sends the real corpus message through it,
 * and reads it back the way the poller does.
 */
class ImapForwardedMessageTest {

  private static final Path EMAILS =
      Path.of("..", "testdata", "corpus", "emails").toAbsolutePath().normalize();

  private static final String USER = "safety@smart-inbox.test";
  private static final String PASSWORD = "test-password";

  @RegisterExtension
  static final GreenMailExtension greenMail = new GreenMailExtension(
      new ServerSetup[] {ServerSetupTest.SMTP, ServerSetupTest.IMAP})
      .withConfiguration(com.icegreen.greenmail.configuration.GreenMailConfiguration
          .aConfig().withUser(USER, USER, PASSWORD))
      .withPerMethodLifecycle(true);

  private ImapMailboxAdapter adapter;
  private MimeWalker walker;

  @BeforeEach
  void setUp() {
    AppProperties props = new AppProperties(
        new AppProperties.Mail(
            "127.0.0.1", ServerSetupTest.IMAP.getPort(), USER, PASSWORD,
            false, "INBOX", 10_000, false, 1),
        new AppProperties.Limits(25, 60),
        new AppProperties.Queue(0, 2, 1000, 300, 60_000, 3),
        new AppProperties.Ai("http://localhost:8000", 300),
        new AppProperties.Storage("./target/test-blobs", "./target/test-renders"),
        new AppProperties.Security("reviewer", "reviewer", "admin", "admin"));
    adapter = new ImapMailboxAdapter(props);
    walker = new MimeWalker(props);
  }

  private void deliver(String key) throws Exception {
    byte[] raw = Files.readAllBytes(EMAILS.resolve(key + ".eml"));
    MimeMessage message = new MimeMessage(
        Session.getInstance(new Properties()), new java.io.ByteArrayInputStream(raw));
    greenMail.getUserManager().getUser(USER).deliver(message);
  }

  @Test
  @DisplayName("E5: over real IMAP, the forwarded PDF arrives with its bytes intact")
  void forwardedAttachmentSurvivesTheImapFetch() throws Exception {
    deliver("adv-02-forwarded-rfc822");

    List<MimeWalker.WalkResult> walked = new ArrayList<>();
    int handled = adapter.fetchUnread(10, message -> walked.add(walker.walk(message)));

    assertThat(handled).isEqualTo(1);
    assertThat(walked).hasSize(1);
    MimeWalker.WalkResult result = walked.get(0);

    assertThat(result.hadForwardedMessage()).isTrue();

    MimeWalker.RawAttachment pdf = result.attachments().stream()
        .filter(a -> a.filename() != null && a.filename().endsWith(".pdf"))
        .findFirst()
        .orElseThrow(() -> new AssertionError(
            "the forwarded PDF was lost entirely; attachments were "
                + result.attachments().stream().map(MimeWalker.RawAttachment::filename).toList()));

    // The exact assertions that failed before the fix:
    assertThat(pdf.filename())
        .as("a lazily-fetched nested part loses its filename and falls back to 'part-1.pdf'")
        .isEqualTo("AER-2026-00188.pdf");
    assertThat(pdf.size())
        .as("this was zero, which sniffed as application/x-empty and was skipped silently")
        .isGreaterThan(1000);
    assertThat(pdf.data())
        .startsWith("%PDF-".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
    assertThat(pdf.nestingLevel()).isEqualTo(1);

    // And the case inside the forward reaches the body text, where classification can see it.
    assertThat(result.textBody()).contains("jaundice", "Fenaquil");
  }

  @Test
  @DisplayName("a plain attachment also survives, and \\Seen is set so it is not re-fetched")
  void ordinaryAttachmentAndSeenFlag() throws Exception {
    deliver("icsr-02-serious-with-form");

    List<MimeWalker.WalkResult> walked = new ArrayList<>();
    assertThat(adapter.fetchUnread(10, m -> walked.add(walker.walk(m)))).isEqualTo(1);

    assertThat(walked.get(0).attachments()).singleElement().satisfies(a -> {
      assertThat(a.filename()).isEqualTo("AER-2026-00188.pdf");
      assertThat(a.size()).isGreaterThan(1000);
    });

    // The \Seen flag is the high-water mark and it lives on the server, so a second poll —
    // or an application restart — must not re-ingest the same message.
    List<MimeWalker.WalkResult> second = new ArrayList<>();
    assertThat(adapter.fetchUnread(10, m -> second.add(walker.walk(m)))).isZero();
    assertThat(second).isEmpty();
  }

  @Test
  @DisplayName("a handler that throws leaves the message unread for the next poll")
  void failedIngestLeavesMessageUnread() throws Exception {
    deliver("icsr-01-complete-body");

    int handled = adapter.fetchUnread(10, message -> {
      throw new IllegalStateException("simulated ingest failure");
    });
    assertThat(handled).as("a failed message is not counted as handled").isZero();

    // Still unread, so the next poll picks it up — no silent loss.
    List<Message> retry = new ArrayList<>();
    assertThat(adapter.fetchUnread(10, retry::add)).isEqualTo(1);
  }
}
