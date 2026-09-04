import { ChangeDetectionStrategy, Component, booleanAttribute, input } from '@angular/core';

/**
 * Panel with an optional header.
 *
 * Content projection keeps the header row flexible: `[card-actions]` lands on the right of the
 * title, everything else falls into the body. `flush` drops body padding for tables that should
 * meet the panel edge.
 */
@Component({
  selector: 'ui-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (heading() || subheading()) {
      <header class="head">
        <div class="titles">
          <h2 class="title">{{ heading() }}</h2>
          @if (subheading()) { <p class="sub">{{ subheading() }}</p> }
        </div>
        <div class="actions"><ng-content select="[card-actions]" /></div>
      </header>
    }
    <div class="body" [class.flush]="flush()"><ng-content /></div>
  `,
  styles: [`
    :host {
      display: block;
      background: var(--surface-1);
      border: 1px solid var(--line);
      border-radius: var(--r-md);
      box-shadow: var(--shadow-1);
      overflow: hidden;
    }
    .head {
      display: flex; align-items: center; gap: 12px;
      padding: 9px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-1);
    }
    .titles { min-width: 0; }
    .title { font-size: 12.5px; font-weight: 650; color: var(--text-1); }
    .sub { font-size: 11.5px; color: var(--text-3); margin-top: 1px; }
    .actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }
    .body { padding: 14px; }
    .body.flush { padding: 0; }
  `],
})
export class UiCardComponent {
  readonly heading = input('');
  readonly subheading = input('');
  readonly flush = input(false, { transform: booleanAttribute });
}
