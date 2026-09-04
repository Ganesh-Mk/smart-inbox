import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService, Overview, QueueRow } from '../api.service';

/**
 * The review queue.
 *
 * Sorted worst-first — anything needing attention, then lowest confidence, then oldest —
 * because that is the order a reviewer's day actually has. The flag chips are the point of the
 * screen: a reviewer should be able to see *why* a message wants them without opening it.
 */
@Component({
  selector: 'app-queue',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './queue.component.html',
  styleUrl: './queue.component.scss',
})
export class QueueComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly rows = signal<QueueRow[]>([]);
  readonly stats = signal<Overview | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly total = signal(0);

  search = '';
  category = '';
  flaggedOnly = false;

  ngOnInit(): void {
    this.load();
    // The pipeline runs in the background, so the queue fills while you watch it.
    setInterval(() => this.load(true), 5000);
  }

  load(quiet = false): void {
    if (!quiet) this.loading.set(true);
    this.api.queue({
      q: this.search || undefined,
      category: this.category || undefined,
      flagged: this.flaggedOnly || undefined,
      size: 100,
    }).subscribe({
      next: (page) => {
        this.rows.set(page.rows);
        this.total.set(page.total);
        this.loading.set(false);
        this.error.set(null);
      },
      error: (err) => {
        this.error.set(err?.message ?? 'Could not load the queue');
        this.loading.set(false);
      },
    });
    this.api.overview().subscribe({ next: (s) => this.stats.set(s) });
  }

  open(row: QueueRow): void {
    this.router.navigate(['/messages', row.messageId]);
  }

  categoriesOf(row: QueueRow): string[] {
    return row.categories ? row.categories.split(',').filter(Boolean) : [];
  }

  /** Colour by what the label means clinically, not by rank. */
  categoryClass(category: string): string {
    switch (category) {
      case 'ICSR': return 'chip chip-icsr';
      case 'ICSR_INCOMPLETE': return 'chip chip-incomplete';
      case 'PQC': return 'chip chip-pqc';
      case 'MI': return 'chip chip-mi';
      default: return 'chip chip-none';
    }
  }

  confidenceClass(value: number | null): string {
    if (value === null || value === undefined) return 'conf conf-unknown';
    if (value >= 0.7) return 'conf conf-high';
    if (value >= 0.4) return 'conf conf-mid';
    return 'conf conf-low';
  }

  percent(value: number | null): string {
    return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;
  }

  /** Every reason this row is asking for a human, in the order a reviewer cares about them. */
  flags(row: QueueRow): { label: string; kind: string; title: string }[] {
    const out: { label: string; kind: string; title: string }[] = [];
    if (row.unverifiedEvidence > 0) {
      out.push({
        label: `${row.unverifiedEvidence} unverified`,
        kind: 'flag flag-amber',
        title: 'The model cited a source, but the quote could not be found in it. '
             + 'Confidence has been capped at 0.40.',
      });
    }
    if (row.conflictCount > 0) {
      out.push({
        label: `${row.conflictCount} conflict`,
        kind: 'flag flag-red',
        title: 'Two sources disagree on this fact. Both values are kept for you to choose between.',
      });
    }
    if (row.failedDocumentCount > 0) {
      out.push({
        label: `${row.failedDocumentCount} unreadable`,
        kind: 'flag flag-red',
        title: 'A document could not be parsed. The message was classified without it.',
      });
    }
    if (row.truncatedDocs > 0) {
      out.push({ label: 'truncated', kind: 'flag flag-amber',
        title: 'A document exceeded the page cap; only the first pages were processed.' });
    }
    if (row.deadJobs > 0) {
      out.push({ label: 'dead job', kind: 'flag flag-red',
        title: 'A pipeline stage failed repeatedly and was dead-lettered.' });
    }
    return out;
  }

  statusClass(status: string): string {
    if (status === 'READY_FOR_REVIEW') return 'status status-ready';
    if (status === 'REVIEWED') return 'status status-done';
    if (status === 'FAILED' || status === 'NEEDS_ATTENTION') return 'status status-bad';
    return 'status status-working';
  }
}
