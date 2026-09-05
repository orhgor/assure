import * as Sentry from "@sentry/browser";

const dsn = window.__SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    tracesSampleRate: 0.1,
  });
}
