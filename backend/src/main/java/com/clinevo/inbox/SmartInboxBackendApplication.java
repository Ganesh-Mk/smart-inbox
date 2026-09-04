package com.clinevo.inbox;

import com.clinevo.inbox.config.AppProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Smart Inbox — AI first-pass triage for a pharmacovigilance shared mailbox.
 *
 * <p>Spring Boot owns state, orchestration and security. It reads mail, writes Oracle, drives the
 * job queue, serves the API and records the audit trail — and it never calls an LLM itself. All
 * inference goes through the stateless Python service (PROJECT_PLAN §5.1).
 */
@SpringBootApplication
@EnableScheduling
@EnableConfigurationProperties(AppProperties.class)
public class SmartInboxBackendApplication {

  public static void main(String[] args) {
    SpringApplication.run(SmartInboxBackendApplication.class, args);
  }
}
