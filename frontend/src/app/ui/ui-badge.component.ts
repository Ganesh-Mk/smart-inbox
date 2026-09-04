import { ChangeDetectionStrategy, Component, booleanAttribute, input } from '@angular/core';

export type Tone = 'neutral' | 'brand' | 'ok' | 'warn' | 'bad' | 'info' | 'violet';

/**
 * A status pill.
 *
 * Tone is a *meaning*, not a colour: callers pass `ok` / `warn` / `bad`, never a hex, so the
 * whole app restates severity consistently and dark mode needs no per-component work.
 */
@Component({
  selector: 'ui-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="badge" [class]="'t-' + tone()" [class.solid]="solid()"
                   [class.sm]="size() === 'sm'"><ng-content /></span>`,
  styles: [`
    :host { display: inline-flex; min-width: 0; }
    .badge {
      display: inline-flex; align-items: center; gap: 4px;
      max-width: 100%; padding: 2px 8px;
      border: 1px solid transparent; border-radius: var(--r-full);
      font-size: 11px; font-weight: 600; line-height: 1.5;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .badge.sm { padding: 1px 6px; font-size: 10.5px; }

    .t-neutral { background: var(--neutral-soft); border-color: var(--neutral-border); color: var(--neutral); }
    .t-brand   { background: var(--brand-soft);   border-color: var(--brand-border);   color: var(--brand-ink); }
    .t-ok      { background: var(--ok-soft);      border-color: var(--ok-border);      color: var(--ok); }
    .t-warn    { background: var(--warn-soft);    border-color: var(--warn-border);    color: var(--warn); }
    .t-bad     { background: var(--bad-soft);     border-color: var(--bad-border);     color: var(--bad); }
    .t-info    { background: var(--info-soft);    border-color: var(--info-border);    color: var(--info); }
    .t-violet  { background: var(--violet-soft);  border-color: var(--violet-border);  color: var(--violet); }

    .badge.solid { color: var(--text-on-brand); border-color: transparent; }
    .t-ok.solid    { background: var(--ok); }
    .t-warn.solid  { background: var(--warn); }
    .t-bad.solid   { background: var(--bad); }
    .t-brand.solid { background: var(--brand); }
  `],
})
export class UiBadgeComponent {
  readonly tone = input<Tone>('neutral');
  readonly size = input<'sm' | 'md'>('md');
  readonly solid = input(false, { transform: booleanAttribute });
}
