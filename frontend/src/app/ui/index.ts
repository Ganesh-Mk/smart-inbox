/** The design system's public surface. Screens import from here, never from a file path. */
export * from './domain';
export * from './ui-badge.component';
export * from './ui-card.component';
export * from './ui-empty.component';
export * from './ui-icon.component';
export * from './ui-meter.component';
export * from './ui-modal.component';
export * from './ui-stat.component';
export * from './ui-tabs.component';

import { UiBadgeComponent } from './ui-badge.component';
import { UiCardComponent } from './ui-card.component';
import { UiEmptyComponent } from './ui-empty.component';
import { UiIconComponent } from './ui-icon.component';
import { UiMeterComponent } from './ui-meter.component';
import { UiModalComponent } from './ui-modal.component';
import { UiStatComponent } from './ui-stat.component';
import { UiTabsComponent } from './ui-tabs.component';

/** Spread into a component's `imports` to get the whole kit. */
export const UI = [
  UiBadgeComponent, UiCardComponent, UiEmptyComponent, UiIconComponent,
  UiMeterComponent, UiModalComponent, UiStatComponent, UiTabsComponent,
] as const;
