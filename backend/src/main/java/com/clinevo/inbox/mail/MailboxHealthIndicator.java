package com.clinevo.inbox.mail;

import java.util.List;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.stereotype.Component;

/**
 * Reports on the mailbox the application actually reads.
 *
 * <p>Spring Boot's own {@code MailHealthIndicator} is deliberately not used. It probes
 * {@code spring.mail.host}/{@code port} as an <em>SMTP</em> server; our port is IMAP, so it
 * reported the whole application DOWN with "Got bad greeting from SMTP host ... * OK
 * IMAP4rev1 Server GreenMail ready" — a failure of a connection this service never makes.
 * Removing the {@code spring.mail} block removes that indicator; this one replaces it with a
 * check of the real dependency, through the same adapter the poller uses.
 */
@Component("mailbox")
public class MailboxHealthIndicator implements HealthIndicator {

  private final MailboxAdapter mailbox;

  public MailboxHealthIndicator(MailboxAdapter mailbox) {
    this.mailbox = mailbox;
  }

  @Override
  public Health health() {
    try {
      List<Integer> counts = mailbox.counts();
      int total = counts.get(0);
      if (total < 0) {
        return Health.down()
            .withDetail("mailbox", mailbox.describe())
            .withDetail("reason", "folder could not be opened")
            .build();
      }
      return Health.up()
          .withDetail("mailbox", mailbox.describe())
          .withDetail("messages", total)
          .withDetail("unseen", counts.get(1))
          .build();
    } catch (RuntimeException e) {
      return Health.down()
          .withDetail("mailbox", mailbox.describe())
          .withDetail("error", e.getClass().getSimpleName() + ": " + e.getMessage())
          .build();
    }
  }
}
