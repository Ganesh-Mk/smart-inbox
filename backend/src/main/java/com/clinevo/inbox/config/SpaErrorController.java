package com.clinevo.inbox.config;

import jakarta.servlet.RequestDispatcher;
import jakarta.servlet.http.HttpServletRequest;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.web.servlet.error.ErrorController;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;

/**
 * Error handling that does not leak Spring's Whitelabel page into a single-page application.
 *
 * <p>Two audiences share one origin here. A request under {@code /api} is a machine asking a
 * question and wants a JSON answer with the message the reviewer UI already knows how to render.
 * Anything else is a person with a URL in their address bar, and the SPA owns those routes —
 * including ones the router has never heard of, which it resolves itself.
 *
 * <p>Previously an unknown path fell through to the default error page: a bare stack-trace-styled
 * screen with a server timestamp, rendered entirely outside the application shell. It looked like
 * a crash, and it exposed framework detail to anyone who mistyped a URL.
 *
 * <p>Requests that look like a file — anything with an extension — are deliberately *not*
 * forwarded. Answering a missing `.js` or `.png` with an HTML page and a 200 turns a clear
 * failure into a confusing one.
 */
@Controller
public class SpaErrorController implements ErrorController {

  @RequestMapping("/error")
  public Object handleError(HttpServletRequest request) {
    Object rawStatus = request.getAttribute(RequestDispatcher.ERROR_STATUS_CODE);
    int status = rawStatus == null ? 500 : Integer.parseInt(rawStatus.toString());

    Object rawPath = request.getAttribute(RequestDispatcher.ERROR_REQUEST_URI);
    String path = rawPath == null ? "" : rawPath.toString();

    if (status == HttpStatus.NOT_FOUND.value() && isSpaRoute(path)) {
      return "forward:/index.html";
    }

    Object message = request.getAttribute(RequestDispatcher.ERROR_MESSAGE);
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("timestamp", OffsetDateTime.now().toString());
    body.put("status", status);
    body.put("error", HttpStatus.resolve(status) == null
        ? "Error" : HttpStatus.valueOf(status).getReasonPhrase());
    body.put("message", message == null || message.toString().isBlank()
        ? "Request failed" : message.toString());
    body.put("path", path);
    return ResponseEntity.status(status).body(body);
  }

  /** A path the Angular router should be given a chance to resolve. */
  private boolean isSpaRoute(String path) {
    if (path.startsWith("/api/") || path.startsWith("/actuator/")) {
      return false;
    }
    int lastSlash = path.lastIndexOf('/');
    String lastSegment = lastSlash < 0 ? path : path.substring(lastSlash + 1);
    return !lastSegment.contains(".");
  }
}
