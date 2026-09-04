import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { ApiService } from '../api.service';
import {
  UI, TabItem, Tone, attentionFlags, categoryTone, fieldStatusTone, humanise, percent,
  sentenceCase, statusTone,
} from '../ui';

interface Highlight {
  documentId: number;
  pageNo: number;
  bbox: [number, number, number, number] | null;
  quote: string;
  verified: boolean;
}

/**
 * Message detail — the screen the whole traceability design exists for.
 *
 * Two panes. The left is the source, permanently visible, because the answer to "why does it say
 * that?" is always a place in a document. The right is tabbed: a reviewer works one question at a
 * time (is the label right? are the facts right? what did the model cost?) and stacking all of it
 * in one column meant scrolling past three screens of tables to reach the decision buttons.
 *
 * The interaction that matters: clicking a field's evidence chip scrolls the left pane to the
 * right document and page and draws a box over the exact quoted text. When the citation could
 * *not* be verified the chip is amber and says so — the system reporting its own hallucination
 * rather than hiding it.
 *
 * There is deliberately no PDF.js here. Pages were rendered to PNG at parse time and the overlay
 * uses the same PyMuPDF coordinate space the extraction came from, so the highlight lines up.
 */
@Component({
  selector: 'app-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, ...UI],
  templateUrl: './detail.component.html',
  styleUrl: './detail.component.scss',
})
export class DetailComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly message = signal<any>(null);
  readonly audit = signal<any[]>([]);
  readonly aiCalls = signal<any[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  readonly activeDocument = signal<any>(null);
  readonly activePage = signal(1);
  readonly highlight = signal<Highlight | null>(null);
  readonly inspectedCall = signal<any>(null);
  readonly activeTab = signal('overview');

  readonly pageImageSrc = signal<string | null>(null);
  private objectUrl: string | null = null;

  /**
   * Re-fetch the page PNG whenever the selected document or page changes. The blob comes back
   * through HttpClient, so it carries the same Basic credentials as every other request; a bare
   * `<img src>` would not, and the browser would prompt for them itself.
   */
  private readonly pageImageLoader = effect((onCleanup) => {
    const doc = this.activeDocument();
    const page = this.currentPage();
    if (!doc || !page || !page.RENDER_PATH) {
      this.setPageImage(null);
      return;
    }
    let cancelled = false;
    onCleanup(() => { cancelled = true; });
    this.api.pageImageBlob(doc.ID, page.PAGE_NO).subscribe({
      next: (blob) => { if (!cancelled) this.setPageImage(URL.createObjectURL(blob)); },
      error: () => { if (!cancelled) this.setPageImage(null); },
    });
  });

  reviewNotes = '';
  editing: { fieldId: number; caseId: number; value: string; status: string } | null = null;

  // ---- derived counts, used for the tab badges ------------------------------------------------

  readonly cases = computed(() => this.message()?.cases ?? []);
  readonly documents = computed(() => this.message()?.documents ?? []);

  readonly allFields = computed<any[]>(() =>
    this.cases().flatMap((c: any) => c.fields ?? []));

  readonly conflictCount = computed(() =>
    this.allFields().filter((f: any) => f.STATUS === 'CONFLICT').length);

  readonly unverifiedCount = computed(() =>
    this.allFields().reduce(
      (n: number, f: any) => n + (f.evidence ?? []).filter((e: any) => e.VERIFIED === 'N').length, 0));

  readonly artefactCount = computed(() =>
    this.documents().reduce(
      (n: number, d: any) => n + (d.tables?.length ?? 0) + (d.images?.length ?? 0), 0));

  readonly failedDocs = computed(() =>
    this.documents().filter((d: any) => d.PARSE_STATUS === 'PARSE_FAILED').length);

  readonly headerFlags = computed(() => attentionFlags({
    unverifiedEvidence: this.unverifiedCount(),
    conflictCount: this.conflictCount(),
    failedDocumentCount: this.failedDocs(),
  }));

  readonly tabs = computed<TabItem[]>(() => [
    { id: 'overview', label: 'Overview', icon: 'shield' },
    {
      id: 'case', label: 'Extracted data', icon: 'table',
      count: this.allFields().length || null,
      tone: this.conflictCount() ? 'bad' : undefined,
    },
    { id: 'sources', label: 'Sources', icon: 'file', count: this.artefactCount() || null },
    { id: 'ai', label: 'AI calls', icon: 'cpu', count: this.aiCalls().length || null },
    { id: 'audit', label: 'Audit', icon: 'clock', count: this.audit().length || null },
  ]);

  readonly totalCost = computed(() =>
    this.aiCalls().reduce((sum: number, c: any) => sum + Number(c.COST_USD ?? 0), 0));

  // ---- lifecycle -------------------------------------------------------------------------------

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    this.load(id);
  }

  ngOnDestroy(): void {
    this.setPageImage(null);
  }

  load(id: number): void {
    this.loading.set(true);
    this.api.message(id).subscribe({
      next: (m) => {
        this.message.set(m);
        const first = (m.documents ?? []).find((d: any) => d.SOURCE_KIND !== 'EMAIL_BODY')
          ?? (m.documents ?? [])[0];
        this.activeDocument.set(first ?? null);
        this.activePage.set(first?.pages?.[0]?.PAGE_NO ?? 1);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.message ?? 'Could not load the message');
        this.loading.set(false);
      },
    });
    this.api.audit(id).subscribe({ next: (a) => this.audit.set(a) });
    this.api.aiCalls(id).subscribe({ next: (c) => this.aiCalls.set(c) });
  }

  back(): void {
    this.router.navigate(['/queue']);
  }

  // ---- the evidence interaction ------------------------------------------------------------

  /**
   * Jump to the source of a fact.
   *
   * An unverified citation still navigates — the reviewer needs to see *where the model claimed*
   * the fact came from in order to judge it — but no highlight box is drawn, because we have no
   * honest coordinates to draw one at.
   */
  showEvidence(evidence: any): void {
    if (!evidence) return;
    const documentId = evidence.DOCUMENT_ID;
    const document = this.documents().find((d: any) => d.ID === documentId);
    if (document) this.activeDocument.set(document);

    const pageNo = evidence.PAGE_NO ?? 1;
    this.activePage.set(pageNo);

    this.highlight.set({
      documentId,
      pageNo,
      bbox: evidence.VERIFIED === 'Y' && evidence.BBOX ? this.parseBbox(evidence.BBOX) : null,
      quote: evidence.QUOTE,
      verified: evidence.VERIFIED === 'Y',
    });
  }

  private parseBbox(raw: string): [number, number, number, number] | null {
    const parts = raw.split(',').map(Number);
    return parts.length === 4 && parts.every((n) => !isNaN(n))
      ? [parts[0], parts[1], parts[2], parts[3]]
      : null;
  }

  /** Convert PDF point coordinates to a percentage overlay on the rendered page image. */
  highlightStyle(): Record<string, string> | null {
    const h = this.highlight();
    const page = this.currentPage();
    if (!h?.bbox || !page || !page.WIDTH || !page.HEIGHT) return null;
    const [x0, y0, x1, y1] = h.bbox;
    return {
      left: `${(x0 / page.WIDTH) * 100}%`,
      top: `${(y0 / page.HEIGHT) * 100}%`,
      width: `${((x1 - x0) / page.WIDTH) * 100}%`,
      height: `${((y1 - y0) / page.HEIGHT) * 100}%`,
    };
  }

  currentPage(): any {
    return (this.activeDocument()?.pages ?? [])
      .find((p: any) => p.PAGE_NO === this.activePage());
  }

  /** Whether this page has a rendered PNG at all, decided synchronously so the text fallback
   *  does not flash while the image request is still in flight. */
  hasPageImage(): boolean {
    const page = this.currentPage();
    return !!this.activeDocument() && !!page && !!page.RENDER_PATH;
  }

  private setPageImage(url: string | null): void {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = url;
    this.pageImageSrc.set(url);
  }

  selectDocument(doc: any): void {
    this.activeDocument.set(doc);
    this.activePage.set(doc.pages?.[0]?.PAGE_NO ?? 1);
    this.highlight.set(null);
  }

  documentLabel(doc: any): string {
    return doc.SOURCE_KIND === 'EMAIL_BODY' ? 'Email body' : doc.FILENAME;
  }

  clearHighlight(): void { this.highlight.set(null); }

  // ---- fields --------------------------------------------------------------------------------

  fieldGroups(caseRow: any): { group: string; fields: any[] }[] {
    const groups = new Map<string, any[]>();
    for (const field of caseRow.fields ?? []) {
      const list = groups.get(field.FIELD_GROUP) ?? [];
      list.push(field);
      groups.set(field.FIELD_GROUP, list);
    }
    const order = ['PATIENT', 'REPORTER', 'PRODUCT', 'REACTION', 'SEVERITY', 'DEFECT',
                   'ENQUIRY', 'NARRATIVE'];
    return [...groups.entries()]
      .sort((a, b) => (order.indexOf(a[0]) + 99) % 99 - (order.indexOf(b[0]) + 99) % 99)
      .map(([group, fields]) => ({ group, fields }));
  }

  /** Conflicts (E33) are the rows a reviewer must actually decide. */
  conflictsIn(caseRow: any): any[] {
    return (caseRow.fields ?? []).filter((f: any) => f.STATUS === 'CONFLICT');
  }

  shortLabel(path: string): string {
    return path.replace(/^[a-z]+\[\d+]\./, '').replace(/^[a-z]+\./, '').replace(/_/g, ' ');
  }

  startEdit(caseRow: any, field: any): void {
    this.editing = {
      fieldId: field.ID, caseId: caseRow.ID,
      value: field.VALUE_TEXT ?? '', status: field.STATUS,
    };
  }

  saveEdit(): void {
    if (!this.editing) return;
    const { caseId, fieldId, value, status } = this.editing;
    this.api.overrideField(caseId, fieldId, value, status, this.reviewNotes).subscribe({
      next: () => {
        this.editing = null;
        this.load(this.message().id);
      },
      error: (err) => this.error.set(err?.error?.message ?? 'Override failed'),
    });
  }

  cancelEdit(): void { this.editing = null; }

  // ---- decisions -------------------------------------------------------------------------------

  decide(decision: 'ACCEPT' | 'OVERRIDE' | 'REJECT'): void {
    const m = this.message();
    const categories = (m.classifications ?? []).map((c: any) => c.CATEGORY);
    this.api.review(m.id, decision, categories, this.reviewNotes).subscribe({
      next: () => this.load(m.id),
      error: (err) => this.error.set(err?.error?.message ?? 'Could not record the decision'),
    });
  }

  reprocess(): void {
    this.api.reprocess(this.message().id).subscribe({ next: () => this.back() });
  }

  inspect(call: any): void {
    this.api.aiCall(call.ID).subscribe({ next: (full) => this.inspectedCall.set(full) });
  }

  closeInspector(): void { this.inspectedCall.set(null); }

  parseJson(raw: string | null): any[] {
    if (!raw) return [];
    try { return JSON.parse(raw); } catch { return []; }
  }

  /** Pretty-print the stored request/response so the inspector is readable, not a single line. */
  prettyJson(raw: string | null): string {
    if (!raw) return '';
    try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; }
  }

  relevanceTone(relevance: string): Tone {
    switch (relevance) {
      case 'RELEVANT': return 'ok';
      case 'POSSIBLY': return 'warn';
      case 'NOT_RELEVANT': return 'neutral';
      default: return 'neutral';
    }
  }

  auditTone(action: string): Tone {
    if (action?.includes('FAIL') || action?.includes('DEAD')) return 'bad';
    if (action?.includes('OVERRIDE') || action?.includes('REJECT')) return 'warn';
    if (action?.includes('ACCEPT') || action?.includes('COMPLETE')) return 'ok';
    return 'neutral';
  }

  // Shared vocabulary, re-exported for the template.
  readonly categoryTone = categoryTone;
  readonly statusTone = statusTone;
  readonly fieldStatusTone = fieldStatusTone;
  readonly humanise = humanise;
  readonly sentenceCase = sentenceCase;
  readonly percent = percent;
}
