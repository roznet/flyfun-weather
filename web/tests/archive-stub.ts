import type { Page } from '@playwright/test';

/** Serve an empty past-flights archive (#1032).
 *
 *  The flights list fetches `GET /api/flights/archive` alongside
 *  `/api/flights` (past rows come only from the archive). A spec that mocks
 *  the list but not the archive lets that request reach the real dev server,
 *  whose dev user has real past flights. Those then show up as extra
 *  `.flight-card`s (`flight-lifecycle` and `multi-delete` saw 21–23). Call this
 *  in any spec that stubs the flights list. */
export async function stubEmptyArchive(page: Page): Promise<void> {
  await page.route(/\/api\/flights\/archive(\?.*)?$/, route => route.fulfill({ json: {
    view: 'list', total: 0, limit: 20, offset: 0, rows: [],
    facets: { source: 'forecast', tags: {}, outcomes: {}, not_recorded: 0 },
  } }));
}
