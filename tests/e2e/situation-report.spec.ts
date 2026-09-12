import { test, expect } from '@playwright/test';

test('scene summary cites events, reveals filtered rows, and handles updates and rewind', async ({ page }) => {
  let commands = 0;
  let updating = false;
  const playback = { run_id: 'sitrep-run', state: 'paused', position_seconds: 30, duration_seconds: 60,
    revision: 0, server_time: new Date().toISOString() };
  const recording = { id: 'cam', camera_label: 'Camera A', original_filename: 'source.mp4', duration_seconds: 60,
    start_offset_seconds: 0, size_bytes: 100, media_url: '/fixture-source.mp4', validation_status: 'ready',
    processing_status: 'transcribing', latest_analyzed_time_seconds: 30 };
  const incident = { id: 'sitrep-ui', title: 'Personnel briefing verification', context: '',
    created_at: new Date().toISOString(), recordings: [recording], playback };
  const event = { id: 'event-1', segment_id: 'source', recording_id: 'cam', timestamp_seconds: 20,
    local_seconds: 20, title: 'Witness clarifies the entrance location', status: 'outdated',
    status_reason: 'The original entrance location was corrected.' };
  const item = (text: string) => ({ text, source_ids: ['source'], event_ids: [event.id] });
  const data = { run_id: playback.run_id, state: 'completed', version_id: 'version-1', known_through: 30,
    stale: false, awaiting_analysis: 0, report: { events: { [event.id]: event }, sources: { source: { id: 'source', recording_id: 'cam',
      camera_label: 'Camera A', local_start: 20, local_end: 30, incident_start: 20, incident_end: 30,
      text: 'I meant the rear entrance. I do not know whether anyone is upstairs.' } }, sections: {
      overview: [item('A witness reports an incident at the rear entrance.')],
      developments: [item('The witness clarified the entrance location.')],
      scene_status: [item('The witness has not established whether anyone is upstairs.')],
      clarifications: [item('Is anyone upstairs? The witness says they do not know.')],
    } } };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/playback') && route.request().method() === 'POST') commands++;
    const body = path.endsWith('/situation-report') ? { ...data, state: updating ? 'processing' : 'completed', stale: updating }
      : path.endsWith('/events') ? { run_id: playback.run_id, configured: true, state: 'idle', events: [{ ...event, status: updating ? 'disproven' : 'outdated' }], pending_segments: 0, processed_segments: 1 }
        : path.endsWith('/transcripts') ? { run_id: playback.run_id, recordings: [], transcription_configured: true }
          : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=sitrep-ui');
  const report = page.getByRole('region', { name: '02 Scene summary', exact: true });
  await expect(report.getByRole('heading', { name: 'What happened' })).toBeVisible();
  await expect(report.getByRole('heading', { name: 'Still unclear' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Scene reconstruction' })).toHaveCount(0);
  const facts = page.locator('#event-history');
  await facts.getByRole('button', { name: 'Current 0', exact: true }).click();
  await expect(page.locator('#event-event-1')).toHaveCount(0);
  await report.getByRole('button', { name: /Event 1: Needs review/ }).first().click();
  await expect(page.locator('#event-event-1')).toBeFocused();
  await expect(page.locator('#event-event-1')).toHaveClass(/cited-event/);
  expect(commands).toBe(0);
  await expect(page.locator('video')).toHaveCount(1);
  updating = true;
  await expect(report.getByText('Updating summary')).toBeVisible();
  await expect(report.getByText('This summary may be out of date', { exact: false })).toBeVisible();
  await expect(report.getByRole('button', { name: /Event 1: Invalidated/ }).first()).toHaveClass(/citation-disproven/);
  await expect(report.getByRole('heading', { name: 'What happened' })).toBeVisible();
  await report.scrollIntoViewIfNeeded();
  await report.screenshot({ path: 'test-results/personnel-situation-report.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await report.scrollIntoViewIfNeeded();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await report.screenshot({ path: 'test-results/personnel-situation-report-mobile.png' });
  playback.position_seconds = 5;
  playback.revision++;
  await expect(report.getByRole('heading', { name: 'What happened' })).toHaveCount(0);
});

test('failed first summary does not claim a previous summary or active generation', async ({ page }) => {
  const playback = { run_id: 'failed-run', state: 'paused', position_seconds: 30, duration_seconds: 60,
    revision: 0, server_time: new Date().toISOString() };
  const incident = { id: 'failed-ui', title: 'Failed summary', context: '',
    created_at: new Date().toISOString(), recordings: [], playback };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith('/situation-report') ? { run_id: playback.run_id, state: 'retrying',
      error: 'The model returned no sentences with valid event citations. Retry summary.',
      known_through: null, report: null, awaiting_analysis: 0, stale: false }
      : path.endsWith('/events') ? { run_id: playback.run_id, configured: true, state: 'idle', events: [], pending_segments: 0, processed_segments: 1 }
      : path.endsWith('/transcripts') ? { run_id: playback.run_id, recordings: [], transcription_configured: true }
      : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=failed-ui');
  const summary = page.getByRole('region', { name: '02 Scene summary' });
  await expect(summary.getByText('Retry scheduled', { exact: true })).toBeVisible();
  await expect(summary.getByRole('alert')).toContainText('no sentences with valid event citations');
  await expect(summary.getByTestId('analysis-empty-state')).toContainText('No summary is available');
  await expect(summary).not.toContainText('previous summary');
  await expect(summary).not.toContainText('Preparing a concise summary');
});
