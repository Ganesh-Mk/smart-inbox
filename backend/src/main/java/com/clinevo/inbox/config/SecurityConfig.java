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
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/actuator/health", "/actuator/info").permitAll()
            .requestMatchers("/swagger-ui/**", "/v3/api-docs/**").permitAll()
            // The built Angular bundle. Filenames are content-hashed (main-BN4H6VEW.js), and
            // the SPA router owns every non-/api path, so the whole static surface is public
            // and the API behind it is not.
            .requestMatchers("/", "/index.html", "/favicon.ico", "/*.js", "/*.css",
                "/*.txt", "/*.json", "/assets/**", "/media/**").permitAll()
            .requestMatchers("/queue", "/queue/**", "/messages/**").permitAll()
            .anyRequest().authenticated())
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
