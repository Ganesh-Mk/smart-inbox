import { ChangeDetectionStrategy, Component, HostListener, input, output } from '@angular/core';
import { UiIconComponent } from './ui-icon.component';

/**
 * Centred dialog. Closes on Escape and on backdrop click, never on a click inside the panel.
 * Used for the AI-call inspector, where the body is long enough to need its own scroll region.
 */
@Component({
  selector: 'ui-modal',
  standalone: true,
  imports: [UiIconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="backdrop" (click)="closed.emit()">
      <div class="panel" role="dialog" aria-modal="true" [style.max-width.px]="width()"
           (click)="$event.stopPropagation()">
        <header class="head">
          <div class="titles">
            <h2 class="title">{{ heading() }}</h2>
            @if (subheading()) { <p class="sub">{{ subheading() }}</p> }
          </div>
          <button class="btn btn--ghost btn--icon btn--sm" (click)="closed.emit()"
                  aria-label="Close">
            <ui-icon name="close" [size]="15" />
          </button>
        </header>
        <div class="body"><ng-content /></div>
      </div>
    </div>
  `,
  styles: [`
    .backdrop {
      position: fixed; inset: 0; z-index: 60;
      display: flex; align-items: center; justify-content: center; padding: 24px;
      background: var(--overlay); backdrop-filter: blur(2px);
      animation: fade .13s ease;
    }
    .panel {
      display: flex; flex-direction: column;
      width: 100%; max-height: 86vh;
      background: var(--surface-1); border: 1px solid var(--line);
      border-radius: var(--r-lg); box-shadow: var(--shadow-3); overflow: hidden;
      animation: rise .15s ease;
    }
    .head {
      display: flex; align-items: flex-start; gap: 12px;
      padding: 12px 14px; border-bottom: 1px solid var(--line);
    }
    .titles { min-width: 0; }
    .title { font-size: 13px; font-weight: 650; }
    .sub { font-size: 11.5px; color: var(--text-3); margin-top: 2px; }
    .head .btn { margin-left: auto; }
    .body { padding: 14px; overflow: auto; }
    @keyframes fade { from { opacity: 0; } }
    @keyframes rise { from { opacity: 0; transform: translateY(6px) scale(.99); } }
  `],
})
export class UiModalComponent {
  readonly heading = input('');
  readonly subheading = input('');
  readonly width = input(880);
  readonly closed = output<void>();

  @HostListener('document:keydown.escape')
  onEscape(): void { this.closed.emit(); }
}
