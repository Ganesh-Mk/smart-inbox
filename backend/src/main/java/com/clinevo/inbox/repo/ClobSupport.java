package com.clinevo.inbox.repo;

import java.sql.ResultSet;
import java.sql.SQLException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

/**
 * Reading CLOB columns without silently truncating them.
 *
 * <p>The obvious way to get a CLOB into a {@code Map<String, Object>} from
 * {@code queryForList} is {@code TO_CHAR(column)}. It works right up until it does not:
 * {@code TO_CHAR} converts to {@code VARCHAR2}, which caps at 4,000 bytes, and Oracle then
 * throws rather than truncating —
 *
 * <pre>ORA-22835: Buffer too small for CLOB to CHAR conversion (actual: 4340, maximum: 4000)</pre>
 *
 * <p>That is a lucky failure mode. It surfaced as a dead-lettered classification job on the
 * first page of real text longer than 4 KB, which is most of them; had Oracle truncated
 * quietly instead, the model would have been handed the first 4,000 characters of every page
 * and the missing text would have shown up as unexplained extraction misses.
 *
 * <p>The fix is to stop converting. Oracle's JDBC driver materialises a CLOB perfectly well
 * through {@link ResultSet#getString}, so the column is selected as itself and read directly.
 */
public final class ClobSupport {

  private ClobSupport() {
  }

  /** Reads a CLOB column as a String, or {@code null}. Never truncates. */
  public static String clob(ResultSet rs, String column) throws SQLException {
    String value = rs.getString(column);
    return value == null || value.isEmpty() ? null : value;
  }

  /** Convenience for a single-value query over one CLOB column. */
  public static String queryClob(JdbcTemplate jdbc, String sql, Object... args) {
    return jdbc.query(sql, (RowMapper<String>) (rs, rowNum) -> clob(rs, 1), args)
        .stream().findFirst().orElse(null);
  }

  private static String clob(ResultSet rs, int index) throws SQLException {
    String value = rs.getString(index);
    return value == null || value.isEmpty() ? null : value;
  }
}
