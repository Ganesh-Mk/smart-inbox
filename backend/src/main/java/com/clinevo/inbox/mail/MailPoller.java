package com.clinevo.inbox.mail;

import com.clinevo.inbox.config.AppProperties;
import com.clinevo.inbox.ingest.IngestService;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Polls the mailbox on a fixed schedule and hands each new message to ingestion.
 *
 * <p>Deliberately dull. The poller does no parsing, no classification and no AI work: it fetches,
 * ingests, and lets the queue take over. Overlapping runs are prevented with a simple flag
 * rather than a lock, because a slow poll should be skipped, not queued up behind itself.
 */
@Component
@ConditionalOnProperty(name = "inbox.mail.poll-enabled", havingValue = "true", matchIfMissing = true)
public class MailPoller {

  private static final Logger log = LoggerFactory.getLogger(MailPoller.class);

  private final MailboxAdapter mailbox;
  private final IngestService ingest;
  private final AppProperties props;

  private final AtomicBoolean polling = new AtomicBoolean(false);
  private final AtomicInteger ingested = new AtomicInteger();
  private final AtomicInteger duplicates = new AtomicInteger();
  private volatile boolean loggedUnavailable = false;

  public MailPoller(MailboxAdapter mailbox, IngestService ingest, AppProperties props) {
    this.mailbox = mailbox;
    this.ingest = ingest;
    this.props = props;
  }

  @Scheduled(
      fixedDelayString = "${inbox.mail.poll-interval-ms:10000}",
      initialDelayString = "${inbox.mail.poll-initial-delay-ms:5000}")
  public void poll() {
    if (!polling.compareAndSet(false, true)) {
      log.debug("Previous poll still running; skipping this tick");
      return;
    }
    try {
      int handled = mailbox.fetchUnread(50, message -> {
        var result = ingest.ingest(message);
        if (result.duplicate()) {
          duplicates.incrementAndGet();
          log.debug("Skipped duplicate (dedupe key already present)");
        } else {
          ingested.incrementAndGet();
        }
      });
      if (handled > 0) {
        log.info("Poll complete: {} message(s) handled — {} ingested, {} duplicates so far",
            handled, ingested.get(), duplicates.get());
      }
      loggedUnavailable = false;
    } catch (RuntimeException e) {
      if (!loggedUnavailable) {
        log.error("Mail poll failed against {}", mailbox.describe(), e);
        loggedUnavailable = true;   // do not fill the log every ten seconds while it is down
      }
    } finally {
      polling.set(false);
    }
  }

  /** Runs one poll immediately — used by the API's "check mail now" action and by tests. */
  public int pollNow() {
    return mailbox.fetchUnread(200, message -> ingest.ingest(message));
  }

  public int ingestedCount() {
    return ingested.get();
  }

  public int duplicateCount() {
    return duplicates.get();
  }

  public String mailboxDescription() {
    return mailbox.describe();
  }
}
