import { Tone } from './ui-badge.component';

/**
 * The shared vocabulary for turning domain values into UI meaning.
 *
 * Both screens route through here so a category, a status or a confidence band never gets a
 * different colour on the queue than it has on the detail screen. Adding a category is one edit.
 */

/** Colour by what the label means clinically, not by rank. */
export function categoryTone(category: string): Tone {
  switch (category) {
    case 'ICSR': return 'ok';
    case 'ICSR_INCOMPLETE': return 'warn';
    case 'PQC': return 'info';
    case 'MI': return 'violet';
    default: return 'neutral';
  }
}

export function statusTone(status: string): Tone {
  switch (status) {
    case 'READY_FOR_REVIEW': return 'ok';
    case 'REVIEWED': return 'neutral';
    case 'FAILED':
    case 'NEEDS_ATTENTION': return 'bad';
    default: return 'info';
  }
}

/** Field-level extraction status. `NOT_STATED` is a correct answer and must never read as a failure. */
export function fieldStatusTone(status: string): Tone {
  switch (status) {
    case 'STATED': return 'ok';
    case 'UNCERTAIN': return 'warn';
    case 'CONFLICT': return 'bad';
    default: return 'neutral';
  }
}

export function confidenceBand(value: number | null | undefined): 'high' | 'mid' | 'low' | 'none' {
  if (value === null || value === undefined) return 'none';
  if (value >= 0.7) return 'high';
  if (value >= 0.4) return 'mid';
  return 'low';
}

export function percent(value: number | null | undefined, dash = '—'): string {
  return value === null || value === undefined ? dash : `${Math.round(value * 100)}%`;
}

/** `ICSR_INCOMPLETE` -> `ICSR incomplete`; `not_stated` -> `not stated`. */
export function humanise(value: string | null | undefined): string {
  if (!value) return '';
  return value.replace(/_/g, ' ');
}

export function sentenceCase(value: string | null | undefined): string {
  const s = humanise(value).toLowerCase();
  return s ? s[0].toUpperCase() + s.slice(1) : '';
}

export interface Flag { label: string; tone: Tone; title: string; }

/**
 * Every reason a message is asking for a human, ordered by what a reviewer cares about first.
 *
 * Shared because the queue shows these as the reason to open a message and the detail header
 * shows the same set as the reason you are looking at it — they must not drift apart.
 */
export function attentionFlags(row: {
  unverifiedEvidence?: number; conflictCount?: number; failedDocumentCount?: number;
  truncatedDocs?: number; deadJobs?: number;
}): Flag[] {
  const out: Flag[] = [];
  if (row.unverifiedEvidence) {
    out.push({
      label: `${row.unverifiedEvidence} unverified`, tone: 'warn',
      title: 'The model cited a source, but the quote could not be found in it. '
           + 'Confidence has been capped at 0.40.',
    });
  }
  if (row.conflictCount) {
    out.push({
      label: `${row.conflictCount} conflict`, tone: 'bad',
      title: 'Two sources disagree on this fact. Both values are kept for you to choose between.',
    });
  }
  if (row.failedDocumentCount) {
    out.push({
      label: `${row.failedDocumentCount} unreadable`, tone: 'bad',
      title: 'A document could not be parsed. The message was classified without it.',
    });
  }
  if (row.truncatedDocs) {
    out.push({
      label: 'truncated', tone: 'warn',
      title: 'A document exceeded the page cap; only the first pages were processed.',
    });
  }
  if (row.deadJobs) {
    out.push({
      label: 'dead job', tone: 'bad',
      title: 'A pipeline stage failed repeatedly and was dead-lettered.',
    });
  }
  return out;
}
