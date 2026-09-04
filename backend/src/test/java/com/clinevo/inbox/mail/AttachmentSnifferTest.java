package com.clinevo.inbox.mail;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/** E4: the declared type is a claim; the bytes are the evidence. */
class AttachmentSnifferTest {

  private static final Path CORPUS =
      Path.of("..", "testdata", "corpus").toAbsolutePath().normalize();

  private final AttachmentSniffer sniffer = new AttachmentSniffer();

  @Test
  @DisplayName("a PDF declared application/octet-stream is still recognised as a PDF")
  void sniffsPdfDespiteWrongDeclaredType() throws IOException {
    // This is the actual corpus file that adv-05 attaches as report_export.dat.
    byte[] data = Files.readAllBytes(CORPUS.resolve("pdfs/form_C08.pdf"));

    String sniffed = sniffer.sniff(data, "report_export.dat");

    assertThat(sniffed).isEqualTo("application/pdf");
    assertThat(sniffer.isPdf(data)).isTrue();
    assertThat(sniffer.skipReason(sniffed, data.length, 25L * 1024 * 1024)).isNull();
  }

  @Test
  @DisplayName("a corrupt PDF still sniffs as a PDF — the damage is only visible on open")
  void corruptPdfStillSniffsAsPdf() throws IOException {
    byte[] data = Files.readAllBytes(CORPUS.resolve("pdfs/corrupt_report.pdf"));

    // Sniffing is about identity, not validity. Calling this "not a PDF" would send it down
    // the UNSUPPORTED_TYPE path and lose the reason it actually failed (E7).
    assertThat(sniffer.sniff(data, "damaged_form.pdf")).isEqualTo("application/pdf");
  }

  @Test
  @DisplayName("a .docx is identified from its zip signature plus its name")
  void identifiesDocx() throws IOException {
    byte[] data = Files.readAllBytes(CORPUS.resolve("assets/reporting_guidance.docx"));

    String sniffed = sniffer.sniff(data, "reporting_guidance.docx");

    assertThat(sniffed)
        .isEqualTo("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    assertThat(sniffer.skipReason(sniffed, data.length, 25L * 1024 * 1024))
        .isEqualTo("UNSUPPORTED_TYPE");
  }

  @Test
  @DisplayName("a plain zip is unsupported; a JPEG is describable")
  void distinguishesZipFromImage() throws IOException {
    byte[] zip = Files.readAllBytes(CORPUS.resolve("assets/case_bundle.zip"));
    byte[] jpeg = Files.readAllBytes(CORPUS.resolve("assets/blister_defect.jpg"));

    assertThat(sniffer.sniff(zip, "case_bundle.zip")).isEqualTo("application/zip");
    assertThat(sniffer.skipReason("application/zip", zip.length, Long.MAX_VALUE))
        .isEqualTo("UNSUPPORTED_TYPE");

    assertThat(sniffer.sniff(jpeg, "blister_defect.jpg")).isEqualTo("image/jpeg");
    assertThat(sniffer.isDescribableImage("image/jpeg")).isTrue();
    // E6: a bare photo of a damaged blister pack is a real PQC artefact, so it is processed.
    assertThat(sniffer.skipReason("image/jpeg", jpeg.length, Long.MAX_VALUE)).isNull();
  }

  @ParameterizedTest(name = "{0} bytes -> {1}")
  @CsvSource({
      "'%PDF-1.7 rest', application/pdf",
      "'GIF89a...', image/gif",
      "'{\\rtf1', application/rtf",
      "'random text', application/octet-stream",
  })
  @DisplayName("magic bytes decide, not the extension")
  void magicBytesDecide(String content, String expected) {
    byte[] data = content.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
    assertThat(sniffer.sniff(data, "anything.xyz")).isEqualTo(expected);
  }

  @Test
  @DisplayName("empty and oversized attachments get their own skip reasons")
  void sizeBasedSkipReasons() {
    assertThat(sniffer.sniff(new byte[0], "empty.pdf")).isEqualTo("application/x-empty");
    assertThat(sniffer.skipReason("application/x-empty", 0, 1000)).isEqualTo("EMPTY");
    assertThat(sniffer.skipReason("application/pdf", 30_000_000, 25L * 1024 * 1024))
        .isEqualTo("TOO_LARGE");
  }
}
