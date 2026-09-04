package com.clinevo.inbox.mail;

import jakarta.mail.Message;
import java.util.List;
import java.util.function.Consumer;

/**
 * The mailbox, behind one interface.
 *
 * <p>There is exactly one IMAP implementation ({@link ImapMailboxAdapter}). The interface
 * exists so tests can drive ingestion from a fixture without a server, not so that a second
 * protocol can be slotted in — building two mail code paths for a prototype would be the wrong
 * trade (E1). Pointing the same implementation at Gmail is four configuration values, which is
 * the README's Gmail recipe.
 */
public interface MailboxAdapter {

  /**
   * Fetches unread messages and hands each to {@code handler}.
   *
   * <p>The callback shape is deliberate: a message is processed and flagged {@code \Seen}
   * inside the same folder session, so a crash between fetching and persisting leaves the
   * message unread and it is simply picked up next time. Returning a {@code List<Message>} to
   * the caller would break that, because a jakarta.mail Message is only valid while its folder
   * is open.
   *
   * @param limit maximum messages to fetch in one poll
   * @param handler called once per message; a thrown exception leaves that message unread
   * @return the number of messages successfully handled
   */
  int fetchUnread(int limit, Consumer<Message> handler);

  /** True when the mailbox can be reached and authenticated against. */
  boolean isAvailable();

  /** Human-readable description of what we are connected to, for logs and the UI. */
  String describe();

  /** Message counts for the health endpoint: {@code [total, unseen]}. */
  List<Integer> counts();
}
