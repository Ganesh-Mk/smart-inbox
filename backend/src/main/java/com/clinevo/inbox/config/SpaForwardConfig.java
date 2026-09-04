package com.clinevo.inbox.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Serves the Angular bundle for client-side routes.
 *
 * <p>The router owns paths like {@code /queue} and {@code /messages/42}. They exist only in the
 * browser — there is no server-side handler — so a deep link or a page refresh on one would
 * otherwise 404. Forwarding them to {@code index.html} lets Angular take over and resolve the
 * route itself.
 *
 * <p>Only the specific route prefixes the application actually has are forwarded, rather than a
 * blanket catch-all. A catch-all would swallow genuine 404s from {@code /api}, turning a
 * mistyped endpoint into a page of HTML and making the mistake much harder to see.
 */
@Configuration
public class SpaForwardConfig implements WebMvcConfigurer {

  @Override
  public void addViewControllers(ViewControllerRegistry registry) {
    registry.addViewController("/queue").setViewName("forward:/index.html");
    registry.addViewController("/queue/**").setViewName("forward:/index.html");
    registry.addViewController("/messages/**").setViewName("forward:/index.html");
    registry.addViewController("/report").setViewName("forward:/index.html");
    registry.addViewController("/literature").setViewName("forward:/index.html");
  }
}
