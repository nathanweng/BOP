import { test, expect } from '@playwright/test';

test('history polls automatically, colors corrections, and shows evidence times', async ({ page }) => {
  // Browser-only fixtures: nothing is inserted into a saved incident or sent to a provider.
  let corrected = false;
  const playback = { run_id: 'history-run', state: 'paused', position_seconds: 0, duration_seconds: 60,
    revision: 0, server_time: new Date().toISOString() };
  const incident = { id: 'history-ui', title: 'History UI verification', context: '',
    created_at: new Date().toISOString(), recordings: [], playback };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const events = [
      { id: 'one', segment_id: 'first', recording_id: 'camera', timestamp_seconds: 2, local_seconds: 0,
        title: 'Speaker reports a knife', status: corrected ? 'disproven' : 'current',
        status_timestamp_seconds: corrected ? 20 : undefined, status_local_seconds: corrected ? 20 : undefined,
        status_recording_id: corrected ? 'camera' : undefined,
        status_reason: corrected ? 'Speaker corrects the object to a phone.' : undefined },
      { id: 'two', segment_id: 'later', recording_id: 'camera', timestamp_seconds: 12, local_seconds: 10,
        title: 'Speaker reports the building empty', status: corrected ? 'outdated' : 'current',
        status_timestamp_seconds: corrected ? 30 : undefined, status_local_seconds: corrected ? 30 : undefined,
        status_recording_id: corrected ? 'camera' : undefined,
        status_reason: corrected ? 'Upstairs has not been checked.' : undefined },
    ];
    const body = path.endsWith('/events') ? { run_id: playback.run_id, configured: true,
      model: 'test', state: 'idle', pending_segments: 0, processed_segments: corrected ? 3 : 1, error: null, events }
      : path.endsWith('/knowledge') ? { run_id: playback.run_id, ready: false, reason: 'Wait until playback ends.',
        completed: 0, expected: 0, failed: 0, state: 'not_built', stale: false, nodes: [], edges: [] }
      : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=history-ui');
  const table = page.locator('.event-history');
  await expect(page.getByRole('region', { name: 'Incident map', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open mind map' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Current facts' })).toBeVisible();
  await expect(table.getByRole('button', { name: /Review source at/ })).toHaveCount(2);
  await expect(table.locator('.event-disproven')).toHaveCount(0);
  corrected = true;
  await expect(page.getByRole('heading', { name: 'Suspicious or invalidated' })).toBeVisible();
  await expect(table.locator('.event-disproven')).toHaveCount(1);
  await expect(table.locator('.event-outdated')).toHaveCount(1);
  await expect(table.locator('.event-disproven')).toHaveCSS('background-color', 'rgb(240, 218, 221)');
  await expect(table.locator('.event-outdated')).toHaveCSS('background-color', 'rgb(240, 230, 188)');
  await expect(page.getByRole('button', { name: /Review evidence at/ })).toHaveCount(2);
  await expect(page.getByText('Upstairs has not been checked.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generate / update history' })).toHaveCount(0);
});
