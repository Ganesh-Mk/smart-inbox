import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', redirectTo: 'queue', pathMatch: 'full' },
  {
    path: 'queue',
    title: 'Review queue — Smart Inbox',
    loadComponent: () => import('./queue/queue.component').then((m) => m.QueueComponent),
  },
  {
    path: 'messages/:id',
    title: 'Message — Smart Inbox',
    loadComponent: () => import('./detail/detail.component').then((m) => m.DetailComponent),
  },
  { path: '**', redirectTo: 'queue' },
];
