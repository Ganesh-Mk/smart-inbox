package com.clinevo.inbox;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.junit.jupiter.api.Tag;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

/**
 * Marks a test that needs the real Oracle from {@code docker compose up}.
 *
 * <p>These are integration tests on purpose. The queue's whole point is {@code FOR UPDATE SKIP
 * LOCKED} and {@code PRAGMA AUTONOMOUS_TRANSACTION} — behaviour that exists only in Oracle. An
 * H2 substitute would test a different program and pass while the real one was broken.
 *
 * <p>The web server is not started: these tests exercise the data layer, and binding a port would
 * only collide with a running application.
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@ActiveProfiles("test")
@Tag("integration")
public @interface OracleIntegrationTest {}
