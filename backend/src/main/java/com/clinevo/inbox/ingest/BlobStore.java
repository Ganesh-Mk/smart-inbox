package com.clinevo.inbox.ingest;

import com.clinevo.inbox.config.AppProperties;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Content-addressed storage for attachment bytes (E9).
 *
 * <p>A blob is stored at {@code <root>/<aa>/<bb>/<sha256>}, so the same PDF attached to five
 * emails occupies one file and — more importantly — carries one identity. Parse results are
 * cached against that identity, which means the second copy of a document costs **zero** LLM
 * calls. On a batch run that is real money, and it is a concrete answer to "how do you keep
 * the cost down?" rather than a claim about efficiency.
 *
 * <p>Multi-megabyte binaries stay out of Oracle; the database holds the hash and the path.
 */
@Component
public class BlobStore {

  private static final Logger log = LoggerFactory.getLogger(BlobStore.class);
  private static final HexFormat HEX = HexFormat.of();

  private final Path root;

  public BlobStore(AppProperties props) {
    this.root = Path.of(props.storage().blobDir()).toAbsolutePath().normalize();
    try {
      Files.createDirectories(root);
    } catch (IOException e) {
      throw new UncheckedIOException("Cannot create blob store at " + root, e);
    }
    log.info("Blob store rooted at {}", root);
  }

  /** What {@link #store} did — and specifically, whether we had seen these bytes before. */
  public record StoredBlob(String sha256, Path path, long size, boolean alreadyExisted) {}

  public StoredBlob store(byte[] data) {
    String hash = sha256(data);
    Path target = pathFor(hash);
    if (Files.exists(target)) {
      // Not an error and not a collision: it is the deduplication working.
      return new StoredBlob(hash, target, sizeOf(target), true);
    }
    try {
      Files.createDirectories(target.getParent());
      // Write to a temporary file and move, so a crash never leaves a half-written blob at a
      // path that claims to be the content of that hash.
      Path temp = Files.createTempFile(target.getParent(), "blob-", ".tmp");
      Files.write(temp, data);
      Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE,
          StandardCopyOption.REPLACE_EXISTING);
      return new StoredBlob(hash, target, data.length, false);
    } catch (IOException e) {
      throw new UncheckedIOException("Failed to store blob " + hash, e);
    }
  }

  public byte[] read(String sha256) {
    try {
      return Files.readAllBytes(pathFor(sha256));
    } catch (IOException e) {
      throw new UncheckedIOException("Blob not readable: " + sha256, e);
    }
  }

  public boolean exists(String sha256) {
    return Files.exists(pathFor(sha256));
  }

  /** The stored path as a string relative to the store root, for the database column. */
  public String relativePath(String sha256) {
    return root.relativize(pathFor(sha256)).toString().replace('\\', '/');
  }

  public Path absolutePath(String sha256) {
    return pathFor(sha256);
  }

  public Path root() {
    return root;
  }

  public static String sha256(byte[] data) {
    try {
      return HEX.formatHex(MessageDigest.getInstance("SHA-256").digest(data));
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException("SHA-256 unavailable", e);
    }
  }

  /** Two levels of fan-out, so no directory ends up with tens of thousands of entries. */
  private Path pathFor(String sha256) {
    return root.resolve(sha256.substring(0, 2))
        .resolve(sha256.substring(2, 4))
        .resolve(sha256);
  }

  private static long sizeOf(Path path) {
    try {
      return Files.size(path);
    } catch (IOException e) {
      return -1;
    }
  }
}
