import { test, expect } from '@playwright/test';

test('knowledge map shows people and connections, filters future evidence and opens an independent source', async ({ page }) => {
  let commands = 0;
  const playback = { run_id: 'knowledge-run', state: 'paused', position_seconds: 12, duration_seconds: 30,
    revision: 0, server_time: new Date().toISOString() };
  const recording = { id: 'cam', camera_label: 'Camera A', original_filename: 'source.mp4', duration_seconds: 30,
    start_offset_seconds: 0, size_bytes: 100, media_url: '/fixture-source.mp4', validation_status: 'ready',
    processing_status: 'transcribing', latest_analyzed_time_seconds: 30 };
  const incident = { id: 'knowledge-ui', title: 'Witness and responder overview', context: '',
    created_at: new Date().toISOString(), recordings: [recording], playback };
  const citation = (id: string, end: number) => ({ id, segment_id: id, recording_id: 'cam',
    observation: 'A witness speaks with an officer about the reported object.', attribution: 'Witness report',
    text: 'I thought it was a knife. It was actually a phone.', local_start: end - 10, local_end: end,
    incident_start: end - 10, incident_end: end });
  const node = (id: string, kind: string, label: string, end = 10) => ({ id, kind, label, description: label,
    uncertainty: '', status: 'active', citations: [citation(id, end)] });
  const graph = { run_id: playback.run_id, ready: false, reason: 'Wait until playback ends.', completed: 3,
    expected: 3, failed: 0, state: 'completed', version_id: 'version', stale: false,
    nodes: [node('person', 'person', 'Unidentified witness'), node('police', 'responder', 'Responding officer'),
      node('claim', 'claim', 'Reported knife'), node('correction', 'claim', 'Correction: phone', 30)],
    edges: [{ ...node('relation', 'associated_with', 'Witness speaks with responding officer'), source: 'person', target: 'police' },
      { ...node('correction-edge', 'contradicts', 'Phone report contradicts knife report', 30), source: 'correction', target: 'claim' }] };
  await page.context().route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/playback') && route.request().method() === 'POST') commands++;
    const body = path.endsWith('/knowledge') ? graph : path.endsWith('/events') ? {
      run_id: playback.run_id, configured: true, state: 'idle', events: [], pending_segments: 0, processed_segments: 3,
    } : path.endsWith('/transcripts') ? { run_id: playback.run_id, recordings: [] }
      : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=knowledge-ui');
  await expect(page.getByRole('region', { name: 'Incident map', exact: true })).toBeVisible();
  await expect(page.getByText('No supported location data for this incident.')).toBeVisible();
  const opened = page.waitForEvent('popup');
  await page.getByRole('link', { name: 'Open mind map' }).click();
  const mapWindow = await opened;
  const map = mapWindow.locator('.knowledge-map');
  await expect(mapWindow).toHaveURL(/view=mindmap/);
  await expect(mapWindow.getByText('Independent map window')).toBeVisible();
  await expect(mapWindow.getByRole('region', { name: 'Incident map', exact: true })).toHaveCount(0);
  await expect(map.getByText('Police & responders', { exact: true })).toBeVisible();
  await expect(map.locator('.knowledge-contradicted')).toHaveCount(1);
  await map.getByRole('checkbox', { name: 'Follow timeline' }).check();
  await expect(map.getByText('Correction: phone', { exact: true })).toHaveCount(0);
  await expect(map.locator('.knowledge-contradicted')).toHaveCount(0);
  await map.getByRole('checkbox', { name: 'Follow timeline' }).uncheck();
  await map.getByRole('button', { name: 'Connection: Witness speaks with responding officer' }).click();
  const inspector = map.getByRole('complementary', { name: 'Map evidence inspector' });
  await expect(inspector.getByRole('heading', { name: 'Witness speaks with responding officer' })).toBeVisible();
  await inspector.getByRole('button', { name: /Open source/ }).click();
  await expect(inspector.getByLabel('Map source video')).toBeVisible();
  expect(commands).toBe(0);
  await mapWindow.screenshot({ path: 'test-results/knowledge-map-window.png', fullPage: true });
});
