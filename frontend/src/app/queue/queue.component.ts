import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService, Overview, QueueRow } from '../api.service';
import {
  UI, Flag, Tone, attentionFlags, categoryTone, humanise, percent, statusTone,
} from '../ui';

type SortKey = 'default' | 'confidence' | 'received' | 'sender';

/**
 * The review queue.
 *
 * Sorted worst-first — anything flagged, then lowest confidence, then oldest — because that is
 * the order a reviewer's day actually has. The flag badges are the point of the screen: a
 * reviewer should see *why* a message wants them without opening it.
 */
@Component({
  selector: 'app-queue',
  standalone: true,
  imports: [CommonModule, FormsModule, ...UI],
  templateUrl: './queue.component.html',
  styleUrl: './queue.component.scss',
})
export class QueueComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly rows = signal<QueueRow[]>([]);
  readonly stats = signal<Overview | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly total = signal(0);
  readonly sort = signal<SortKey>('default');

  search = '';
  category = '';
  flaggedOnly = false;

  private poll?: ReturnType<typeof setInterval>;

  /** Client-side re-sort. The server's order is the meaningful default; these are for scanning. */
  readonly view = computed(() => {
    const list = [...this.rows()];
    switch (this.sort()) {
      case 'confidence':
        return list.sort((a, b) => (a.minFieldConfidence ?? 2) - (b.minFieldConfidence ?? 2));
      case 'received':
        return list.sort((a, b) => +new Date(b.receivedAt) - +new Date(a.receivedAt));
      case 'sender':
        return list.sort((a, b) =>
          (a.senderName || a.senderEmail).localeCompare(b.senderName || b.senderEmail));
      default:
        return list;
    }
  });

  readonly attentionCount = computed(() =>
    this.rows().filter((r) => r.needsAttention === 'Y').length);

  ngOnInit(): void {
    this.load();
    // The pipeline runs in the background, so the queue fills while you watch it.
    this.poll = setInterval(() => this.load(true), 5000);
  }

  ngOnDestroy(): void {
    clearInterval(this.poll);
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

  resetFilters(): void {
    this.search = ''; this.category = ''; this.flaggedOnly = false;
    this.sort.set('default');
    this.load();
  }

  get hasFilters(): boolean {
    return !!this.search || !!this.category || this.flaggedOnly;
  }

  open(row: QueueRow): void {
    this.router.navigate(['/messages', row.messageId]);
  }

  categoriesOf(row: QueueRow): string[] {
    return row.categories ? row.categories.split(',').filter(Boolean) : [];
  }

  flags(row: QueueRow): Flag[] { return attentionFlags(row); }

  // Re-exported so the template can reach the shared vocabulary.
  readonly categoryTone = categoryTone;
  readonly statusTone = statusTone;
  readonly humanise = humanise;
  readonly percent = percent;

  initials(row: QueueRow): string {
    const source = (row.senderName || row.senderEmail || '?').trim();
    const parts = source.split(/[\s.@_-]+/).filter(Boolean);
    return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
  }

  /** A stable hue per sender, so the same person keeps the same avatar across sessions. */
  avatarHue(row: QueueRow): number {
    const key = row.senderEmail || row.senderName || '';
    let hash = 0;
    for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) % 360;
    return hash;
  }

  confidenceTone(value: number | null): Tone {
    if (value === null || value === undefined) return 'neutral';
    if (value >= 0.7) return 'ok';
    if (value >= 0.4) return 'warn';
    return 'bad';
  }
}
