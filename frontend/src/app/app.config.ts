import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { routes } from './app.routes';

/**
 * HTTP Basic on every request.
 *
 * A deliberate prototype simplification (PROJECT_PLAN §8.4): its purpose is to make the
 * reviewer's identity real in the audit trail, not to be a security boundary. Credentials come
 * from the same in-memory users the backend declares.
 */
const basicAuth = 'Basic ' + btoa('reviewer:reviewer');

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([
      (req, next) => next(req.clone({ setHeaders: { Authorization: basicAuth } })),
    ])),
  ],
};
