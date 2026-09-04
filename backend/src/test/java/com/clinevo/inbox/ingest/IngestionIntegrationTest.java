package com.clinevo.inbox.ingest;

import static org.assertj.core.api.Assertions.assertThat;

import com.clinevo.inbox.OracleIntegrationTest;
import jakarta.mail.Session;
import jakarta.mail.internet.MimeMessage;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * End-to-end ingestion against the real Oracle, driven by the real corpus files.
 *
 * <p>Each test names the edge case it proves. Together they are the P2 exit criteria: messages
 * land with the right documents, the right attachment rows, and no duplicates.
 */
@OracleIntegrationTest
class IngestionIntegrationTest {

  private static final Path EMAILS =
      Path.of("..", "testdata", "corpus", "emails").toAbsolutePath().normalize();

  @Autowired IngestService ingest;
  @Autowired JdbcTemplate jdbc;
  @Autowired BlobStore blobs;

  @BeforeEach
  void clean() {
    // JOB deliberately has no foreign key to DOCUMENT — the queue is a standalone table so it
    // can outlive the row it refers to and still be inspected. That means deleting documents
    // leaves orphaned job rows behind, which would inflate the counts below, so they are
    // cleared explicitly. Queue-test jobs use subject ids from 900000 up and are left alone.
    jdbc.update("DELETE FROM job WHERE subject_type = 'DOCUMENT' AND subject_id < 900000");
    jdbc.update("DELETE FROM inbox_message");
  }

  private MimeMessage load(String key) throws Exception {
    try (InputStream in = Files.newInputStream(EMAILS.resolve(key + ".eml"))) {
      return new MimeMessage(Session.getInstance(new Properties()), in);
    }
  }

  @Test
  @DisplayName("E11: an email with no attachment still produces an EMAIL_BODY document")
  void bodyOnlyMessageStillGetsADocument() throws Exception {
    var result = ingest.ingest(load("icsr-01-complete-body"));

    assertThat(result.duplicate()).isFalse();
    assertThat(result.documentCount()).isEqualTo(1);

    Map<String, Object> document = jdbc.queryForMap(
        "SELECT source_kind, parse_status, filename FROM document WHERE message_id = ?",
        result.messageId());
    assertThat(document.get("SOURCE_KIND")).isEqualTo("EMAIL_BODY");
    assertThat(document.get("PARSE_STATUS")).isEqualTo("PENDING");

    String body = jdbc.queryForObject(
        "SELECT TO_CHAR(body_text) FROM inbox_message WHERE id = ?",
        String.class, result.messageId());
    assertThat(body).contains("Velmoradine", "maculopapular rash");
  }

  @Test
  @DisplayName("E2: ingesting the same message twice creates exactly one row")
  void reIngestingIsIdempotent() throws Exception {
    var first = ingest.ingest(load("icsr-02-serious-with-form"));
    var second = ingest.ingest(load("icsr-02-serious-with-form"));

    assertThat(first.duplicate()).isFalse();
    assertThat(second.duplicate()).as("the second pass must be recognised as a duplicate").isTrue();
    assertThat(second.messageId()).isEqualTo(first.messageId());

    Integer messages = jdbc.queryForObject(
        "SELECT COUNT(*) FROM inbox_message", Integer.class);
    assertThat(messages).isEqualTo(1);

    Integer documents = jdbc.queryForObject(
        "SELECT COUNT(*) FROM document WHERE message_id = ?", Integer.class, first.messageId());
    assertThat(documents).as("no extra documents from the duplicate pass").isEqualTo(2);
  }

  @Test
  @DisplayName("E4: a PDF sent as application/octet-stream is stored with both types recorded")
  void recordsDeclaredAndSniffedTypeSeparately() throws Exception {
    var result = ingest.ingest(load("adv-05-mislabelled-octet-stream"));

    Map<String, Object> attachment = jdbc.queryForMap(
        "SELECT filename, declared_type, sniffed_type, processed, skip_reason"
            + " FROM message_attachment WHERE message_id = ?", result.messageId());

    assertThat(attachment.get("FILENAME")).isEqualTo("report_export.dat");
    assertThat(attachment.get("DECLARED_TYPE")).isEqualTo("application/octet-stream");
    assertThat(attachment.get("SNIFFED_TYPE")).isEqualTo("application/pdf");
    assertThat(attachment.get("PROCESSED")).isEqualTo("Y");
    assertThat(attachment.get("SKIP_REASON")).isNull();

    // It became a real document despite the wrong declared type.
    Integer pdfDocs = jdbc.queryForObject(
        "SELECT COUNT(*) FROM document WHERE message_id = ? AND source_kind = 'PDF_ATTACHMENT'",
        Integer.class, result.messageId());
    assertThat(pdfDocs).isEqualTo(1);
  }

  @Test
  @DisplayName("E6: .docx and .zip are logged with a skip reason and produce no document")
  void unsupportedTypesAreLoggedNotDropped() throws Exception {
    var result = ingest.ingest(load("adv-04-unsupported-types"));

    List<Map<String, Object>> attachments = jdbc.queryForList(
        "SELECT filename, sniffed_type, processed, skip_reason FROM message_attachment"
            + " WHERE message_id = ? ORDER BY filename", result.messageId());

    assertThat(attachments).hasSize(2);
    assertThat(attachments).allSatisfy(row -> {
      assertThat(row.get("PROCESSED")).isEqualTo("N");
      assertThat(row.get("SKIP_REASON")).isEqualTo("UNSUPPORTED_TYPE");
    });

    // Only the email body became a document — the attachments are recorded, not processed.
    assertThat(result.documentCount()).isEqualTo(1);
    assertThat(result.attachmentCount()).isEqualTo(2);
  }

  @Test
  @DisplayName("E9: the same PDF under two names is stored once and costs one parse")
  void duplicateAttachmentSharesOneBlob() throws Exception {
    var result = ingest.ingest(load("adv-01-duplicate-pdf"));

    List<Map<String, Object>> attachments = jdbc.queryForList(
        "SELECT filename, sha256, blob_path FROM message_attachment WHERE message_id = ?"
            + " ORDER BY filename", result.messageId());

    assertThat(attachments).hasSize(2);
    String hashA = (String) attachments.get(0).get("SHA256");
    String hashB = (String) attachments.get(1).get("SHA256");
    assertThat(hashA)
        .as("identical content must produce one content address, whatever it is called")
        .isEqualTo(hashB);
    assertThat(attachments.get(0).get("BLOB_PATH")).isEqualTo(attachments.get(1).get("BLOB_PATH"));

    // One file on disk, not two.
    assertThat(blobs.exists(hashA)).isTrue();

    // Both documents point at the same content hash, so the parse cache can serve the second
    // for free — this is where the saving actually lands.
    List<String> hashes = jdbc.queryForList(
        "SELECT content_sha256 FROM document WHERE message_id = ?"
            + " AND source_kind = 'PDF_ATTACHMENT'", String.class, result.messageId());
    assertThat(hashes).hasSize(2).containsOnly(hashA);
  }

  @Test
  @DisplayName("E5: a forwarded case is hoisted with its nesting level recorded")
  void forwardedAttachmentsAreHoisted() throws Exception {
    var result = ingest.ingest(load("adv-02-forwarded-rfc822"));

    Map<String, Object> attachment = jdbc.queryForMap(
        "SELECT filename, sniffed_type, nesting_level, processed FROM message_attachment"
            + " WHERE message_id = ? AND sniffed_type = 'application/pdf'", result.messageId());

    assertThat(attachment.get("FILENAME")).isEqualTo("AER-2026-00188.pdf");
    assertThat(((Number) attachment.get("NESTING_LEVEL")).intValue())
        .as("recorded as one level down, not flattened into a plain attachment")
        .isEqualTo(1);
    assertThat(attachment.get("PROCESSED")).isEqualTo("Y");

    String body = jdbc.queryForObject(
        "SELECT TO_CHAR(body_text) FROM inbox_message WHERE id = ?",
        String.class, result.messageId());
    assertThat(body)
        .as("the inner case must reach the body text, or classification sees only 'FYI'")
        .contains("jaundice", "Fenaquil");
  }

  @Test
  @DisplayName("E10: the quoted-history offset is stored so the UI can dim it")
  void quotedOffsetIsRecorded() throws Exception {
    var result = ingest.ingest(load("adv-08-quoted-reply-chain"));

    Integer offset = jdbc.queryForObject(
        "SELECT quoted_offset FROM inbox_message WHERE id = ?", Integer.class, result.messageId());
    assertThat(offset).isNotNull().isPositive();

    String body = jdbc.queryForObject(
        "SELECT TO_CHAR(body_text) FROM inbox_message WHERE id = ?",
        String.class, result.messageId());
    assertThat(body.substring(0, offset)).contains("completely resolved");
    assertThat(body.substring(offset)).contains("Velmoradine");
  }

  @Test
  @DisplayName("every ingested document is enqueued for parsing — nothing is processed inline")
  void enqueuesOneParseJobPerDocument() throws Exception {
    var result = ingest.ingest(load("icsr-08-scanned-form"));

    assertThat(result.documentCount()).isEqualTo(3);   // body + scan + digital form

    Integer jobs = jdbc.queryForObject(
        "SELECT COUNT(*) FROM job WHERE job_type = 'PARSE_DOCUMENT' AND state = 'PENDING'"
            + " AND subject_id IN (SELECT id FROM document WHERE message_id = ?)",
        Integer.class, result.messageId());
    assertThat(jobs).isEqualTo(3);

    String status = jdbc.queryForObject(
        "SELECT status FROM inbox_message WHERE id = ?", String.class, result.messageId());
    assertThat(status).isEqualTo("PARSING");
  }

  @Test
  @DisplayName("the whole corpus ingests: 38 messages, no duplicates, expected document counts")
  void wholeCorpusIngests() throws Exception {
    List<Path> files;
    try (var stream = Files.list(EMAILS)) {
      files = stream.filter(p -> p.toString().endsWith(".eml")).sorted().toList();
    }
    assertThat(files).as("run: python -m testdata.generator.build").hasSize(38);

    int ingested = 0;
    for (Path file : files) {
      try (InputStream in = Files.newInputStream(file)) {
        var result = ingest.ingest(new MimeMessage(Session.getInstance(new Properties()), in));
        if (!result.duplicate()) {
          ingested++;
        }
      }
    }

    assertThat(ingested).isEqualTo(38);

    Integer messages = jdbc.queryForObject("SELECT COUNT(*) FROM inbox_message", Integer.class);
    assertThat(messages).isEqualTo(38);

    Integer documents = jdbc.queryForObject("SELECT COUNT(*) FROM document", Integer.class);
    // Every message contributes an EMAIL_BODY document, plus one per processable attachment.
    assertThat(documents).isGreaterThanOrEqualTo(38 + 20);

    Integer jobs = jdbc.queryForObject(
        "SELECT COUNT(*) FROM job j WHERE j.job_type = 'PARSE_DOCUMENT'"
            + " AND EXISTS (SELECT 1 FROM document d WHERE d.id = j.subject_id)", Integer.class);
    assertThat(jobs).as("exactly one parse job per document, and none left inline")
        .isEqualTo(documents);

    // A second full pass must add nothing at all (E2).
    for (Path file : files) {
      try (InputStream in = Files.newInputStream(file)) {
        ingest.ingest(new MimeMessage(Session.getInstance(new Properties()), in));
      }
    }
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM inbox_message", Integer.class))
        .as("polling twice must never duplicate a case")
        .isEqualTo(38);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM document", Integer.class))
        .isEqualTo(documents);
  }
}
