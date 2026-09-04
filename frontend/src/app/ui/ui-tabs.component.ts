import { ChangeDetectionStrategy, Component, ElementRef, booleanAttribute, model, input, viewChild } from '@angular/core';
import { UiBadgeComponent } from './ui-badge.component';
import { UiIconComponent, IconName } from './ui-icon.component';

export interface TabItem {
  id: string;
  label: string;
  icon?: IconName;
  /** Rendered as a count pill; `0` and `null` are hidden so quiet tabs stay quiet. */
  count?: number | null;
  /** Draws attention to a tab holding something the reviewer must resolve. */
  tone?: 'warn' | 'bad';
}

/**
 * Tab bar.
 *
 * It renders the strip and owns the active id via a two-way `model`; the parent switches panels
 * itself with `@switch`. That is deliberate — content-projection tabs would have to keep every
 * panel instantiated or fight the router for state, and here the panels are heavy (page images,
 * long tables) and genuinely want to be destroyed when hidden.
 *
 * Arrow keys move between tabs, per the WAI-ARIA tabs pattern.
 */
@Component({
  selector: 'ui-tabs',
  standalone: true,
  imports: [UiBadgeComponent, UiIconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="strip" role="tablist" #strip [class.stretch]="stretch()">
      @for (t of tabs(); track t.id) {
        <button role="tab" class="tab" [class.active]="active() === t.id"
                [attr.aria-selected]="active() === t.id"
                [attr.tabindex]="active() === t.id ? 0 : -1"
                (click)="active.set(t.id)" (keydown)="onKey($event)">
          @if (t.icon) { <ui-icon [name]="t.icon" [size]="14" /> }
          <span class="label">{{ t.label }}</span>
          @if (t.count) {
            <ui-badge size="sm" [tone]="t.tone ?? 'neutral'">{{ t.count }}</ui-badge>
          }
        </button>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .strip {
      display: flex; align-items: stretch; gap: 2px;
      border-bottom: 1px solid var(--line);
      overflow-x: auto; scrollbar-width: none;
    }
    .strip::-webkit-scrollbar { display: none; }
    .strip.stretch .tab { flex: 1; justify-content: center; }

    .tab {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 8px 12px; margin-bottom: -1px;
      font: 500 12.5px/1 var(--font); color: var(--text-2);
      background: transparent; border: 0; border-bottom: 2px solid transparent;
      cursor: pointer; white-space: nowrap;
      transition: color .13s ease, border-color .13s ease, background .13s ease;
    }
    .tab:hover { color: var(--text-1); background: var(--surface-2); }
    .tab.active { color: var(--brand-ink); border-bottom-color: var(--brand); font-weight: 600; }
    .tab:focus-visible { box-shadow: var(--ring); border-radius: var(--r-xs) var(--r-xs) 0 0; }
  `],
})
export class UiTabsComponent {
  readonly tabs = input.required<TabItem[]>();
  readonly active = model.required<string>();
  readonly stretch = input(false, { transform: booleanAttribute });

  private readonly strip = viewChild<ElementRef<HTMLElement>>('strip');

  onKey(event: KeyboardEvent): void {
    const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const list = this.tabs();
    const next = list[(list.findIndex((t) => t.id === this.active()) + delta + list.length) % list.length];
    this.active.set(next.id);
    const el = this.strip()?.nativeElement.querySelectorAll<HTMLElement>('.tab')[list.indexOf(next)];
    el?.focus();
  }
}
