import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { Tone } from './ui-badge.component';

/** A single KPI in the header strip. Compact by design — six of these share one row. */
@Component({
  selector: 'ui-stat',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stat" [class]="'t-' + tone()" [attr.title]="hint() || null">
      <div class="value">{{ value() }}<span class="suffix">{{ suffix() }}</span></div>
      <div class="label">{{ label() }}</div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .stat {
      padding: 7px 13px;
      border-left: 2px solid var(--line-strong);
      min-width: 0;
    }
    .stat[title] { cursor: help; }
    .value {
      font-size: 16px; font-weight: 650; line-height: 1.2; color: var(--text-1);
      font-variant-numeric: tabular-nums; white-space: nowrap;
    }
    .suffix { font-size: 11px; font-weight: 500; color: var(--text-3); margin-left: 2px; }
    .label {
      font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em;
      color: var(--text-3); white-space: nowrap;
    }
    .t-ok    { border-left-color: var(--ok); }
    .t-warn  { border-left-color: var(--warn); }
    .t-bad   { border-left-color: var(--bad); }
    .t-brand { border-left-color: var(--brand); }
    .t-ok .value    { color: var(--ok); }
    .t-warn .value  { color: var(--warn); }
    .t-bad .value   { color: var(--bad); }
  `],
})
export class UiStatComponent {
  readonly label = input.required<string>();
  readonly value = input.required<string | number>();
  readonly suffix = input('');
  readonly hint = input('');
  readonly tone = input<Tone | 'none'>('none');
}
