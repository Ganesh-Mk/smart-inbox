package com.clinevo.inbox.mail;

import com.clinevo.inbox.config.AppProperties;
import jakarta.mail.Flags;
import jakarta.mail.Folder;
import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.Session;
import jakarta.mail.Store;
import jakarta.mail.internet.MimeMessage;
import jakarta.mail.search.FlagTerm;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Real IMAP over jakarta.mail.
 *
 * <p>This is a genuine IMAP client talking to a genuine IMAP server — GreenMail in Docker by
 * default. Nothing about the protocol handling is mocked or simplified for the demo; the same
 * class, with four different property values, connects to Gmail (E1).
 */
@Component
public class ImapMailboxAdapter implements MailboxAdapter {

  private static final Logger log = LoggerFactory.getLogger(ImapMailboxAdapter.class);

  private final AppProperties.Mail config;

  public ImapMailboxAdapter(AppProperties props) {
    this.config = props.mail();
  }

  @Override
  public int fetchUnread(int limit, Consumer<Message> handler) {
    int handled = 0;
    try (Store store = connect()) {
      Folder folder = store.getFolder(config.folder());
      // READ_WRITE because we flag \Seen — that flag is the high-water mark, and it lives on
      // the server, so restarting the application does not re-ingest the mailbox.
      folder.open(Folder.READ_WRITE);
      try {
        Message[] unread = folder.search(new FlagTerm(new Flags(Flags.Flag.SEEN), false));
        int take = Math.min(unread.length, limit);
        if (unread.length > take) {
          log.info("{} unread message(s) waiting; taking {} this poll", unread.length, take);
        }
        for (int i = 0; i < take; i++) {
          Message message = unread[i];
          try {
            handler.accept(materialise(message));
            // Only after the handler has committed. A crash before this point leaves the
            // message unread and it is simply picked up on the next poll.
            message.setFlag(Flags.Flag.SEEN, true);
            handled++;
          } catch (RuntimeException e) {
            log.error("Failed to ingest message '{}'; leaving it unread for the next poll",
                safeSubject(message), e);
          }
        }
      } finally {
        folder.close(false);
      }
    } catch (MessagingException e) {
      log.error("IMAP poll against {} failed", describe(), e);
    }
    return handled;
  }

  @Override
  public boolean isAvailable() {
    try (Store store = connect()) {
      return store.isConnected();
    } catch (MessagingException e) {
      log.debug("Mailbox {} not reachable: {}", describe(), e.getMessage());
      return false;
    }
  }

  @Override
  public List<Integer> counts() {
    try (Store store = connect()) {
      Folder folder = store.getFolder(config.folder());
      folder.open(Folder.READ_ONLY);
      try {
        return List.of(folder.getMessageCount(), folder.getUnreadMessageCount());
      } finally {
        folder.close(false);
      }
    } catch (MessagingException e) {
      return List.of(-1, -1);
    }
  }

  @Override
  public String describe() {
    return String.format("imap%s://%s@%s:%d/%s",
        config.ssl() ? "s" : "", config.user(), config.host(), config.port(), config.folder());
  }

  private Store connect() throws MessagingException {
    String protocol = config.ssl() ? "imaps" : "imap";
    Properties props = new Properties();
    props.put("mail.store.protocol", protocol);
    props.put("mail." + protocol + ".host", config.host());
    props.put("mail." + protocol + ".port", String.valueOf(config.port()));
    props.put("mail." + protocol + ".connectiontimeout", "10000");
    props.put("mail." + protocol + ".timeout", "30000");

    // Use BODY.PEEK[] instead of BODY[] when fetching content.
    //
    // Without this the server sets \Seen the moment we read the body (RFC 3501 §6.4.5), which
    // quietly breaks the retry guarantee: a message whose ingestion throws would already be
    // marked read, so the next poll skips it and the case is lost with no error anywhere. The
    // flag is meant to be *our* high-water mark, set only after the handler has committed.
    props.put("mail." + protocol + ".peek", "true");
    if (config.ssl()) {
      props.put("mail." + protocol + ".ssl.enable", "true");
      // Gmail and Outlook both need this; GreenMail's self-signed certificate needs the trust
      // relaxation only when someone points this at the container's TLS ports.
      props.put("mail." + protocol + ".ssl.trust", config.host());
    }
    // Keep the raw bytes available so DedupeService can hash exactly what arrived.
    props.put("mail.mime.address.strict", "false");

    Session session = Session.getInstance(props);
    Store store = session.getStore(protocol);
    store.connect(config.host(), config.port(), config.user(), config.password());
    return store;
  }

  /**
   * Reads the whole message into memory and re-parses it as a plain {@link MimeMessage}.
   *
   * <p>This is not defensive tidying — without it, ingestion is quietly wrong. A jakarta.mail
   * {@code IMAPMessage} fetches its parts lazily from the folder's connection, and a nested
   * {@code message/rfc822} part cannot be read reliably once the stream position has moved on.
   * The symptom is nasty precisely because it is silent: the forwarded PDF arrives as a
   * <em>zero-byte</em> part with no filename, gets sniffed as {@code application/x-empty}, is
   * recorded with {@code skip_reason='EMPTY'}, and the case inside the forward disappears —
   * with nothing in the log to say anything went wrong.
   *
   * <p>It does not reproduce when parsing an {@code .eml} from disk, because a file-backed
   * message is fully materialised already. It only appears against a real server, which is why
   * {@code ImapForwardedMessageTest} drives a real IMAP server rather than a fixture.
   *
   * <p>Materialising also means the handler no longer depends on the folder staying open, and
   * the exact bytes that arrived are available for hashing.
   */
  private static MimeMessage materialise(Message message) throws MessagingException {
    try (ByteArrayOutputStream buffer = new ByteArrayOutputStream()) {
      message.writeTo(buffer);
      return new MimeMessage(
          Session.getInstance(new Properties()),
          new ByteArrayInputStream(buffer.toByteArray()));
    } catch (IOException e) {
      throw new MessagingException("Could not read message from the server", e);
    }
  }

  private static String safeSubject(Message message) {
    try {
      return message.getSubject();
    } catch (MessagingException e) {
      return "<unreadable subject>";
    }
  }

  /** Convenience for tests and diagnostics: read without flagging. */
  public List<String> peekSubjects(int limit) {
    List<String> subjects = new ArrayList<>();
    try (Store store = connect()) {
      Folder folder = store.getFolder(config.folder());
      folder.open(Folder.READ_ONLY);
      try {
        Message[] all = folder.getMessages();
        for (int i = 0; i < Math.min(all.length, limit); i++) {
          subjects.add(safeSubject(all[i]));
        }
      } finally {
        folder.close(false);
      }
    } catch (MessagingException e) {
      log.warn("peekSubjects failed", e);
    }
    return subjects;
  }
}
