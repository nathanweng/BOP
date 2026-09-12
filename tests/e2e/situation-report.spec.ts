import { test, expect } from '@playwright/test';

test('personnel briefing combines scene context and clarification with independent source review', async ({ page }) => {
  let commands = 0;
  let updating = false;
  const playback = { run_id: 'sitrep-run', state: 'paused', position_seconds: 30, duration_seconds: 60,
    revision: 0, server_time: new Date().toISOString() };
  const recording = { id: 'cam', camera_label: 'Camera A', original_filename: 'source.mp4', duration_seconds: 60,
    start_offset_seconds: 0, size_bytes: 100, media_url: '/fixture-source.mp4', validation_status: 'ready',
    processing_status: 'transcribing', latest_analyzed_time_seconds: 30 };
  const incident = { id: 'sitrep-ui', title: 'Personnel briefing verification', context: '',
    created_at: new Date().toISOString(), recordings: [recording], playback };
  const item = (text: string) => ({ text, source_ids: ['source'] });
  const data = { run_id: playback.run_id, state: 'completed', version_id: 'version-1', known_through: 30,
    stale: false, awaiting_analysis: 0, report: { sources: { source: { id: 'source', recording_id: 'cam',
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
      : path.endsWith('/events') ? { run_id: playback.run_id, configured: true, state: 'idle', events: [], pending_segments: 0, processed_segments: 1 }
        : path.endsWith('/transcripts') ? { run_id: playback.run_id, recordings: [], transcription_configured: true }
          : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=sitrep-ui');
  const report = page.getByRole('region', { name: 'Situational report', exact: true });
  await expect(report.getByRole('heading', { name: 'Scene overview' })).toBeVisible();
  await expect(report.getByRole('heading', { name: 'Clarification needed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Statement-based reconstruction' })).toHaveCount(0);
  await report.getByRole('button', { name: /Review supporting sources/ }).first().click();
  await expect(report.getByLabel('Briefing source video')).toBeVisible();
  expect(commands).toBe(0);
  updating = true;
  await expect(report.getByText('Updating briefing')).toBeVisible();
  await expect(report.getByText('New analyzed evidence is not included yet', { exact: false })).toBeVisible();
  await expect(report.getByRole('heading', { name: 'Scene overview' })).toBeVisible();
  await report.getByRole('button', { name: 'Close briefing evidence' }).click();
  await report.evaluate((element) => element.scrollIntoView({ block: 'start', behavior: 'instant' }));
  await report.screenshot({ path: 'test-results/personnel-situation-report.png' });
  playback.position_seconds = 5;
  playback.revision++;
  await expect(report.getByRole('heading', { name: 'Scene overview' })).toHaveCount(0);
});
