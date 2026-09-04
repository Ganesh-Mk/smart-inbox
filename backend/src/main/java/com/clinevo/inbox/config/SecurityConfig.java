package com.clinevo.inbox.config;

import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.crypto.factory.PasswordEncoderFactories;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import org.springframework.security.web.SecurityFilterChain;

import java.util.List;

/**
 * HTTP Basic with two in-memory roles.
 *
 * <p>A deliberate prototype simplification, declared as such in the write-up (§8.4). Its purpose
 * is not access control — it is to make the reviewer's identity real, so every REVIEW_DECISION and
 * AUDIT_EVENT row names an actual person rather than "anonymous".
 */
@Configuration
// Only meaningful in a servlet application. Data-layer integration tests run with
// WebEnvironment.NONE, where HttpSecurity does not exist and this configuration would
// otherwise fail the context.
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
public class SecurityConfig {

  @Bean
  public PasswordEncoder passwordEncoder() {
    return PasswordEncoderFactories.createDelegatingPasswordEncoder();
  }

  @Bean
  public UserDetailsService userDetailsService(AppProperties props, PasswordEncoder encoder) {
    UserDetails reviewer =
        User.withUsername(props.security().reviewerUser())
            .password(encoder.encode(props.security().reviewerPassword()))
            .roles("REVIEWER")
            .build();
    UserDetails admin =
        User.withUsername(props.security().adminUser())
            .password(encoder.encode(props.security().adminPassword()))
            .roles("REVIEWER", "ADMIN")
            .build();
    return new InMemoryUserDetailsManager(reviewer, admin);
  }

  @Bean
  public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http.csrf(AbstractHttpConfigurer::disable)
        .cors(cors -> cors.configurationSource(corsConfigurationSource()))
        // The API is the security boundary. Everything else — the content-hashed Angular
        // bundle, the SPA's own routes, and the assorted paths a browser probes for on its
        // own — is public.
        //
        // This used to be the other way round: an allow-list of static paths with
        // `.anyRequest().authenticated()` behind it. That challenged every *unknown* path,
        // and a 401 carrying `WWW-Authenticate: Basic` makes the browser open its own
        // sign-in dialog over the application. Browsers speculatively request a surprising
        // number of things that do not exist here — `/manifest.json`,
        // `/apple-touch-icon.png`, `.js.map` when devtools is open, and
        // `/.well-known/appspecific/com.chrome.devtools.json` whenever devtools or an
        // extension debugger attaches. Each one produced a credentials prompt. A path that
        // simply is not here should answer 404, not demand a password.
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/api/**").authenticated()
            .requestMatchers("/actuator/health", "/actuator/info").permitAll()
            .requestMatchers("/actuator/**").authenticated()
            .anyRequest().permitAll())
        .httpBasic(basic -> {});
    return http.build();
  }

  @Bean
  public CorsConfigurationSource corsConfigurationSource() {
    CorsConfiguration config = new CorsConfiguration();
    // ng serve during development; the built bundle is served same-origin for the demo.
    config.setAllowedOrigins(List.of("http://localhost:4200"));
    config.setAllowedMethods(List.of("GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"));
    config.setAllowedHeaders(List.of("*"));
    config.setAllowCredentials(true);
    UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
    source.registerCorsConfiguration("/**", config);
    return source;
  }
}
