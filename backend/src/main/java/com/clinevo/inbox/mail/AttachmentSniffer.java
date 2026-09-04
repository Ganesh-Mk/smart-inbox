package com.clinevo.inbox.mail;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import org.springframework.stereotype.Component;

/**
 * Works out what an attachment actually is, from its bytes (E4).
 *
 * <p>Neither the declared {@code Content-Type} nor the filename extension is trusted. Both are
 * written by the sender's mail client and both are routinely wrong: safety mailboxes receive
 * PDFs declared {@code application/octet-stream} because a document management system stripped
 * the type on export, and {@code .dat} files that are perfectly good PDFs.
 *
 * <p>The declared type is still recorded alongside the sniffed one. Them disagreeing is not an
 * error to paper over — it is an audit-interesting fact about where the message came from.
 */
@Component
public class AttachmentSniffer {

  private static final byte[] PDF_MAGIC = "%PDF-".getBytes(StandardCharsets.US_ASCII);
  private static final byte[] ZIP_MAGIC = {0x50, 0x4B, 0x03, 0x04};
  private static final byte[] PNG_MAGIC = {(byte) 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A};
  private static final byte[] JPEG_MAGIC = {(byte) 0xFF, (byte) 0xD8, (byte) 0xFF};
  private static final byte[] GIF_MAGIC = "GIF8".getBytes(StandardCharsets.US_ASCII);
  private static final byte[] RTF_MAGIC = "{\\rtf".getBytes(StandardCharsets.US_ASCII);
  private static final byte[] OLE_MAGIC =
      {(byte) 0xD0, (byte) 0xCF, 0x11, (byte) 0xE0, (byte) 0xA1, (byte) 0xB1, 0x1A, (byte) 0xE1};

  /** How many leading bytes are enough to identify every type we care about. */
  public static final int SNIFF_WINDOW = 1024;

  /**
   * The real media type of {@code data}, or {@code application/octet-stream} when unrecognised.
   *
   * <p>OOXML files (.docx, .xlsx, .pptx) are zips, so the zip signature is refined using the
   * filename — the only case where the extension gets a vote, and only to distinguish between
   * two things we are going to skip anyway.
   */
  public String sniff(byte[] data, String filename) {
    if (data == null || data.length == 0) {
      return "application/x-empty";
    }
    if (startsWith(data, PDF_MAGIC)) {
      return "application/pdf";
    }
    if (startsWith(data, PNG_MAGIC)) {
      return "image/png";
    }
    if (startsWith(data, JPEG_MAGIC)) {
      return "image/jpeg";
    }
    if (startsWith(data, GIF_MAGIC)) {
      return "image/gif";
    }
    if (startsWith(data, RTF_MAGIC)) {
      return "application/rtf";
    }
    if (startsWith(data, OLE_MAGIC)) {
      return legacyOfficeType(filename);
    }
    if (startsWith(data, ZIP_MAGIC)) {
      return ooxmlType(filename);
    }
    return "application/octet-stream";
  }

  /** True when the bytes really are a PDF, whatever the message claimed. */
  public boolean isPdf(byte[] data) {
    return startsWith(data, PDF_MAGIC);
  }

  /**
   * True for an image we are willing to describe with the vision model.
   *
   * <p>A bare photograph of a damaged blister pack is a genuine PQC artefact, so it gets the
   * same cheap description as an image embedded in a PDF. That is one extra branch for a
   * visible payoff (E6).
   */
  public boolean isDescribableImage(String sniffedType) {
    return "image/png".equals(sniffedType)
        || "image/jpeg".equals(sniffedType)
        || "image/gif".equals(sniffedType);
  }

  /** Why an attachment was not content-processed, or {@code null} when it will be. */
  public String skipReason(String sniffedType, long sizeBytes, long maxBytes) {
    if ("application/x-empty".equals(sniffedType) || sizeBytes == 0) {
      return "EMPTY";
    }
    if (sizeBytes > maxBytes) {
      return "TOO_LARGE";
    }
    if ("application/pdf".equals(sniffedType) || isDescribableImage(sniffedType)) {
      return null;
    }
    return "UNSUPPORTED_TYPE";
  }

  private static String ooxmlType(String filename) {
    String lower = filename == null ? "" : filename.toLowerCase(Locale.ROOT);
    if (lower.endsWith(".docx")) {
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    }
    if (lower.endsWith(".xlsx")) {
      return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    }
    if (lower.endsWith(".pptx")) {
      return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    }
    return "application/zip";
  }

  private static String legacyOfficeType(String filename) {
    String lower = filename == null ? "" : filename.toLowerCase(Locale.ROOT);
    if (lower.endsWith(".doc")) {
      return "application/msword";
    }
    if (lower.endsWith(".xls")) {
      return "application/vnd.ms-excel";
    }
    if (lower.endsWith(".msg")) {
      return "application/vnd.ms-outlook";
    }
    return "application/x-ole-storage";
  }

  private static boolean startsWith(byte[] data, byte[] magic) {
    if (data == null || data.length < magic.length) {
      return false;
    }
    for (int i = 0; i < magic.length; i++) {
      if (data[i] != magic[i]) {
        return false;
      }
    }
    return true;
  }
}
