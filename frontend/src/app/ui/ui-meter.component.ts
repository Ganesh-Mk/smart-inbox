import { ChangeDetectionStrategy, Component, booleanAttribute, computed, input } from '@angular/core';

/**
 * Confidence meter.
 *
 * The `capped` state is the one that matters: when code lowered the model's own number — because
 * a citation could not be verified, a page was illegible, or two sources disagreed — the bar is
 * striped and a ghost tick marks where the model had claimed to be. A reviewer can see at a
 * glance that the system argued with itself, which is the entire point of the confidence chain.
 */
@Component({
  selector: 'ui-meter',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <div class="track" [attr.title]="hint()">
        <div class="fill" [class]="'t-' + band()" [class.capped]="isCapped()"
             [style.width.%]="pct()"></div>
        @if (isCapped()) {
          <span class="ghost" [style.left.%]="preAdjustPct()"
                title="The model claimed {{ preAdjustPct() }}% before verification"></span>
        }
      </div>
      @if (showLabel()) {
        <span class="pct" [class]="'ink-' + band()">
          {{ value() === null ? '—' : pct() + '%' }}
          @if (isCapped()) { <span class="arrow" [attr.title]="hint()">↓</span> }
        </span>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .wrap { display: flex; align-items: center; gap: 7px; }
    .track {
      position: relative; flex: 1; min-width: 44px; height: 5px;
      background: var(--surface-3); border-radius: var(--r-full); overflow: hidden;
    }
    .fill { height: 100%; border-radius: var(--r-full); transition: width .2s ease; }
    .t-high { background: var(--ok); }
    .t-mid  { background: var(--warn); }
    .t-low  { background: var(--bad); }
    .t-none { background: var(--text-3); }

    /* Striped = a number code lowered, not a number the model chose. */
    .fill.capped {
      background-image: linear-gradient(115deg,
        rgb(255 255 255 / 42%) 25%, transparent 25%, transparent 50%,
        rgb(255 255 255 / 42%) 50%, rgb(255 255 255 / 42%) 75%, transparent 75%);
      background-size: 7px 7px;
    }
    .ghost {
      position: absolute; top: -2px; width: 1px; height: 9px;
      background: var(--text-3); opacity: .75;
    }

    .pct {
      min-width: 34px; text-align: right;
      font-size: 11.5px; font-weight: 600; font-variant-numeric: tabular-nums;
    }
    .ink-high { color: var(--ok); }
    .ink-mid  { color: var(--warn); }
    .ink-low  { color: var(--bad); }
    .ink-none { color: var(--text-3); }
    .arrow { color: var(--text-3); font-weight: 700; cursor: help; }
  `],
})
export class UiMeterComponent {
  readonly value = input<number | null>(null);
  /** What the model said before the deterministic confidence chain adjusted it. */
  readonly preAdjust = input<number | null>(null);
  readonly reason = input<string | null>(null);
  readonly showLabel = input(true, { transform: booleanAttribute });

  readonly pct = computed(() => Math.round((this.value() ?? 0) * 100));
  readonly preAdjustPct = computed(() => Math.round((this.preAdjust() ?? 0) * 100));

  readonly isCapped = computed(() => {
    const v = this.value(); const p = this.preAdjust();
    return v !== null && p !== null && p - v > 0.005;
  });

  readonly band = computed(() => {
    const v = this.value();
    if (v === null || v === undefined) return 'none';
    if (v >= 0.7) return 'high';
    if (v >= 0.4) return 'mid';
    return 'low';
  });

  readonly hint = computed(() =>
    this.isCapped()
      ? `Model said ${this.preAdjustPct()}%, code lowered it to ${this.pct()}%. ${this.reason() ?? ''}`.trim()
      : (this.reason() ?? 'No adjustment applied'));
}
