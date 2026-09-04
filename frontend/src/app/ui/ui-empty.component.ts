import { ChangeDetectionStrategy, Component, booleanAttribute, input } from '@angular/core';
import { UiIconComponent, IconName } from './ui-icon.component';

/** Loading / empty / error, so those three states never get improvised per screen. */
@Component({
  selector: 'ui-empty',
  standalone: true,
  imports: [UiIconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap" [class.error]="kind() === 'error'" [class.pad]="padded()">
      @if (kind() === 'loading') {
        <div class="spinner" aria-hidden="true"></div>
      } @else {
        <ui-icon [name]="kind() === 'error' ? 'alert' : (icon() ?? 'inbox')" [size]="22" />
      }
      <div class="title">{{ heading() }}</div>
      @if (message()) { <div class="msg"><ng-content /></div> }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .wrap {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 8px; padding: 26px 20px; text-align: center; color: var(--text-3);
    }
    .wrap.pad { padding: 60px 20px; }
    .wrap.error { color: var(--bad); }
    .title { font-size: 13px; font-weight: 600; color: var(--text-2); }
    .wrap.error .title { color: var(--bad); }
    .msg { font-size: 12px; max-width: 46ch; line-height: 1.55; }
    .spinner {
      width: 20px; height: 20px; border-radius: 50%;
      border: 2px solid var(--line-strong); border-top-color: var(--brand);
      animation: spin .7s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class UiEmptyComponent {
  readonly kind = input<'loading' | 'empty' | 'error'>('empty');
  readonly heading = input('');
  readonly icon = input<IconName | null>(null);
  readonly message = input(true, { transform: booleanAttribute });
  readonly padded = input(false, { transform: booleanAttribute });
}
