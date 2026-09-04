package com.clinevo.inbox.ingest;

import com.clinevo.inbox.audit.AuditService;
import com.clinevo.inbox.config.AppProperties;
import com.clinevo.inbox.mail.AttachmentSniffer;
import com.clinevo.inbox.mail.MimeWalker;
import com.clinevo.inbox.mail.QuotedTextDetector;
import com.clinevo.inbox.queue.JobQueueRepository;
import com.clinevo.inbox.queue.JobType;
import com.clinevo.inbox.queue.SubjectType;
import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.internet.InternetAddress;
import java.sql.Timestamp;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Date;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Turns one email into rows, then hands the work to the queue.
 *
 * <p>The shape of what gets created matters more than it looks:
 *
 * <ul>
 *   <li><b>The email body is itself a {@code DOCUMENT}</b> with {@code source_kind =
 *       EMAIL_BODY} (E11). A perfectly valid ICSR often arrives as prose with no attachment at
 *       all. Making the body a document means bodies and PDFs share one parse path, one
 *       evidence model, one confidence model and one review UI — instead of a second, weaker
 *       code path for the case that happens most often.
 *   <li><b>Nothing is processed synchronously.</b> Ingest writes rows, enqueues one
 *       {@code PARSE_DOCUMENT} per document, and returns. The AI work happens on the worker
 *       pool (R18).
 *   <li><b>Non-PDFs are recorded, not dropped</b> (E6), with the reason. A reviewer asking
 *       "was there anything else attached?" gets an answer.
 * </ul>
 */
@Service
public class IngestService {

  private static final Logger log = LoggerFactory.getLogger(IngestService.class);

  private final JdbcTemplate jdbc;
  private final MimeWalker walker;
  private final AttachmentSniffer sniffer;
  private final QuotedTextDetector quotedTextDetector;
  private final DedupeService dedupe;
  private final BlobStore blobs;
  private final JobQueueRepository queue;
  private final AuditService audit;
  private final AppProperties props;

  public IngestService(
      JdbcTemplate jdbc,
      MimeWalker walker,
      AttachmentSniffer sniffer,
      QuotedTextDetector quotedTextDetector,
      DedupeService dedupe,
      BlobStore blobs,
      JobQueueRepository queue,
      AuditService audit,
      AppProperties props) {
    this.jdbc = jdbc;
    this.walker = walker;
    this.sniffer = sniffer;
    this.quotedTextDetector = quotedTextDetector;
    this.dedupe = dedupe;
    this.blobs = blobs;
    this.queue = queue;
    this.audit = audit;
    this.props = props;
  }

  /** What one ingest attempt did. {@code duplicate} is a normal outcome, not a failure. */
  public record IngestResult(
      Long messageId, boolean duplicate, int documentCount, int attachmentCount, String dedupeKey) {}

  @Transactional
  public IngestResult ingest(Message message) {
    MimeWalker.WalkResult walked = walker.walk(message);
    String bodyText = walked.textBody() == null ? "" : walked.textBody();

    DedupeService.DedupeKey key = dedupe.computeKey(message, bodyText);

    Long existing = findExisting(key.value());
    if (existing != null) {
      log.debug("Message already ingested as {} (dedupe key via {})", existing, key.source());
      return new IngestResult(existing, true, 0, 0, key.value());
    }

    // E10: where does the quoted history start? Stored as an offset so the UI can dim that
    // region and the prompt can de-prioritise it, without ever destroying the text.
    int quoteBoundary = quotedTextDetector.findQuoteBoundary(bodyText);

    long messageId;
    try {
      messageId = insertMessage(message, walked, bodyText, key, quoteBoundary);
    } catch (DuplicateKeyException e) {
      // Another poller thread won the race. The unique constraint is the referee (E2).
      Long winner = findExisting(key.value());
      log.debug("Lost the dedupe race for key {}; existing row is {}", key.value(), winner);
      return new IngestResult(winner, true, 0, 0, key.value());
    }

    // The body is always a document (E11).
    long bodyDocId = insertBodyDocument(messageId, bodyText);
    int documents = 1;
    int attachments = 0;

    for (MimeWalker.RawAttachment raw : walked.attachments()) {
      attachments++;
      Long documentId = persistAttachment(messageId, raw);
      if (documentId != null) {
        documents++;
      }
    }

    jdbc.update("UPDATE inbox_message SET status = 'PARSING' WHERE id = ?", messageId);

    // Enqueue after the rows exist. The transaction commits before a worker can claim these,
    // because the worker's dequeue runs in its own connection.
    for (Long documentId : documentIdsFor(messageId)) {
      queue.enqueue(JobType.PARSE_DOCUMENT, SubjectType.DOCUMENT, documentId);
    }

    audit.systemForMessage(
        "MESSAGE_INGESTED", "INBOX_MESSAGE", messageId,
        String.format("{\"dedupe_source\":\"%s\",\"documents\":%d,\"attachments\":%d,"
                + "\"forwarded\":%b,\"quoted_offset\":%s}",
            key.source(), documents, attachments, walked.hadForwardedMessage(),
            quoteBoundary < 0 ? "null" : String.valueOf(quoteBoundary)),
        messageId);

    log.info("Ingested message {} — {} document(s), {} attachment(s){}",
        messageId, documents, attachments,
        walked.hadForwardedMessage() ? ", contained a forwarded message" : "");

    return new IngestResult(messageId, false, documents, attachments, key.value());
  }

  // ---- persistence -------------------------------------------------------------------

  private Long findExisting(String dedupeKey) {
    List<Long> ids = jdbc.queryForList(
        "SELECT id FROM inbox_message WHERE dedupe_key = ?", Long.class, dedupeKey);
    return ids.isEmpty() ? null : ids.get(0);
  }

  private long insertMessage(
      Message message,
      MimeWalker.WalkResult walked,
      String bodyText,
      DedupeService.DedupeKey key,
      int quoteBoundary) {

    InternetAddress from = firstFrom(message);
    KeyHolder keys = new GeneratedKeyHolder();

    jdbc.update(connection -> {
      var ps = connection.prepareStatement(
          "INSERT INTO inbox_message (dedupe_key, message_id_hdr, folder, sender_email,"
              + " sender_name, recipient_email, subject, sent_at, received_at, body_text,"
              + " body_html, body_charset, quoted_offset, status)"
              + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'RECEIVED')",
          new String[] {"id"});
      ps.setString(1, key.value());
      ps.setString(2, key.messageIdHeader());
      ps.setString(3, props.mail().folder());
      ps.setString(4, from == null ? null : from.getAddress());
      ps.setString(5, from == null ? null : from.getPersonal());
      ps.setString(6, props.mail().user());
      ps.setString(7, truncate(subjectOf(message), 1000));
      ps.setTimestamp(8, toTimestamp(sentDate(message)));
      ps.setTimestamp(9, Timestamp.from(java.time.Instant.now()));
      ps.setString(10, bodyText);
      ps.setString(11, walked.htmlBody());
      ps.setString(12, walked.charset());
      if (quoteBoundary < 0) {
        ps.setNull(13, java.sql.Types.NUMERIC);
      } else {
        ps.setInt(13, quoteBoundary);
      }
      return ps;
    }, keys);

    Number id = keys.getKey();
    if (id == null) {
      throw new IllegalStateException("INBOX_MESSAGE insert returned no generated key");
    }
    return id.longValue();
  }

  private long insertBodyDocument(long messageId, String bodyText) {
    String hash = BlobStore.sha256(bodyText.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    KeyHolder keys = new GeneratedKeyHolder();
    jdbc.update(connection -> {
      var ps = connection.prepareStatement(
          "INSERT INTO document (message_id, source_kind, filename, content_sha256, page_count,"
              + " doc_rendering, doc_genre, parse_status)"
              + " VALUES (?, 'EMAIL_BODY', 'email-body.txt', ?, 1, 'DIGITAL', 'LETTER', 'PENDING')",
          new String[] {"id"});
      ps.setLong(1, messageId);
      ps.setString(2, hash);
      return ps;
    }, keys);
    return keys.getKey().longValue();
  }

  /**
   * Stores one attachment and, when it is something we can understand, creates a document.
   *
   * @return the new document id, or {@code null} when the attachment was recorded but skipped
   */
  private Long persistAttachment(long messageId, MimeWalker.RawAttachment raw) {
    byte[] data = raw.data();
    String sniffed = sniffer.sniff(data, raw.filename());
    long maxBytes = props.limits().maxAttachmentBytes();
    String skipReason = raw.skipReason() != null
        ? raw.skipReason()
        : sniffer.skipReason(sniffed, raw.size(), maxBytes);

    BlobStore.StoredBlob blob = data.length == 0 ? null : blobs.store(data);
    String sha = blob == null ? null : blob.sha256();

    if (!sniffed.equals(raw.declaredType())) {
      log.info("Attachment '{}' declared {} but sniffed {} (E4)",
          raw.filename(), raw.declaredType(), sniffed);
    }

    KeyHolder keys = new GeneratedKeyHolder();
    jdbc.update(connection -> {
      var ps = connection.prepareStatement(
          "INSERT INTO message_attachment (message_id, filename, declared_type, sniffed_type,"
              + " size_bytes, sha256, blob_path, nesting_level, processed, skip_reason)"
              + " VALUES (?,?,?,?,?,?,?,?,?,?)",
          new String[] {"id"});
      ps.setLong(1, messageId);
      ps.setString(2, truncate(raw.filename(), 500));
      ps.setString(3, truncate(raw.declaredType(), 200));
      ps.setString(4, sniffed);
      ps.setLong(5, raw.size());
      ps.setString(6, sha);
      ps.setString(7, sha == null ? null : blobs.relativePath(sha));
      ps.setInt(8, raw.nestingLevel());
      ps.setString(9, skipReason == null ? "Y" : "N");
      ps.setString(10, skipReason);
      return ps;
    }, keys);
    long attachmentId = keys.getKey().longValue();

    if (skipReason != null) {
      log.info("Attachment '{}' recorded but not processed: {} (E6)", raw.filename(), skipReason);
      return null;
    }

    boolean isPdf = "application/pdf".equals(sniffed);
    String sourceKind = isPdf ? "PDF_ATTACHMENT" : "IMAGE_ATTACHMENT";

    KeyHolder docKeys = new GeneratedKeyHolder();
    jdbc.update(connection -> {
      var ps = connection.prepareStatement(
          "INSERT INTO document (message_id, attachment_id, source_kind, filename,"
              + " content_sha256, blob_path, parse_status)"
              + " VALUES (?,?,?,?,?,?,'PENDING')",
          new String[] {"id"});
      ps.setLong(1, messageId);
      ps.setLong(2, attachmentId);
      ps.setString(3, sourceKind);
      ps.setString(4, truncate(raw.filename(), 500));
      ps.setString(5, sha);
      ps.setString(6, sha == null ? null : blobs.relativePath(sha));
      return ps;
    }, docKeys);

    return docKeys.getKey().longValue();
  }

  private List<Long> documentIdsFor(long messageId) {
    return jdbc.queryForList(
        "SELECT id FROM document WHERE message_id = ? ORDER BY id", Long.class, messageId);
  }

  // ---- helpers -----------------------------------------------------------------------

  private static InternetAddress firstFrom(Message message) {
    try {
      var from = message.getFrom();
      if (from != null && from.length > 0 && from[0] instanceof InternetAddress address) {
        return address;
      }
    } catch (MessagingException e) {
      // fall through
    }
    return null;
  }

  private static String subjectOf(Message message) {
    try {
      return message.getSubject();
    } catch (MessagingException e) {
      return null;
    }
  }

  private static Date sentDate(Message message) {
    try {
      return message.getSentDate();
    } catch (MessagingException e) {
      return null;
    }
  }

  private static Timestamp toTimestamp(Date date) {
    return date == null
        ? Timestamp.from(OffsetDateTime.now(ZoneOffset.UTC).toInstant())
        : new Timestamp(date.getTime());
  }

  private static String truncate(String value, int max) {
    if (value == null) {
      return null;
    }
    return value.length() <= max ? value : value.substring(0, max);
  }
}
