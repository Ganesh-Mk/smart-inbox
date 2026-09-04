import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { UiBadgeComponent, UiIconComponent } from './ui';
import { ThemeService } from './theme.service';

/**
 * Application shell: a fixed top bar and a single scrolling work area.
 *
 * The "synthetic data only" marker is deliberately permanent rather than a dismissible notice —
 * a pharmacovigilance screen showing patient-shaped data should never leave the viewer guessing
 * whether it is real.
 */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, UiBadgeComponent, UiIconComponent],
  template: `
    <header class="topbar">
      <div class="brand">
        <span class="mark" aria-hidden="true"><ui-icon name="shield" [size]="16" /></span>
        <span class="name">Smart Inbox</span>
      </div>
      <span class="tagline">Pharmacovigilance first-pass triage</span>

      <span class="spacer"></span>

      <ui-badge tone="warn" size="sm">Synthetic data only</ui-badge>

      <button class="btn btn--ghost btn--icon theme" (click)="theme.toggle()"
              [title]="theme.theme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
              aria-label="Toggle colour theme">
        <ui-icon [name]="theme.theme() === 'dark' ? 'sun' : 'moon'" [size]="15" />
      </button>
    </header>

    <main class="work"><router-outlet /></main>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100vh; }

    .topbar {
      display: flex; align-items: center; gap: 12px;
      height: var(--topbar-h); flex: none; padding: 0 16px;
      background: var(--surface-1);
      border-bottom: 1px solid var(--line);
      box-shadow: var(--shadow-1);
      z-index: 20;
    }
    .brand { display: flex; align-items: center; gap: 8px; }
    .mark {
      display: grid; place-items: center; width: 26px; height: 26px;
      border-radius: var(--r-sm); background: var(--brand); color: var(--text-on-brand);
    }
    .name { font-size: 14.5px; font-weight: 680; letter-spacing: -.01em; }
    .tagline {
      font-size: 12px; color: var(--text-3);
      padding-left: 12px; border-left: 1px solid var(--line);
    }
    .theme { color: var(--text-2); }

    .work { flex: 1; min-height: 0; overflow: hidden; }

    @media (max-width: 820px) { .tagline { display: none; } }
  `],
})
export class App {
  readonly theme = inject(ThemeService);
}
