import { test, expect } from '@playwright/test';

test('automatic build shows real progress and becomes playable without a build click', async ({ page }) => {
  let ready = false;
  let builds = 0;
  const playback = { run_id: 'progress-run', state: 'ended', position_seconds: 20, duration_seconds: 20,
    revision: 0, server_time: new Date().toISOString() };
  const incident = { id: 'progress-incident', title: 'Automatic board', recordings: [], playback };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/knowledge') && route.request().method() === 'POST') builds++;
    await route.fulfill({ json: path.endsWith('/knowledge') ? {
      run_id: playback.run_id, ready: true, state: ready ? 'completed' : 'processing',
      completed: 2, expected: 2, failed: 0, reason: '', stale: false,
      version_id: ready ? 'saved-version' : null, nodes: [], edges: [],
      progress: { stage: ready ? 'ready' : 'analyzing', completed_batches: 1, total_batches: 2 },
    } : path.endsWith('/playback') ? playback : incident });
  });
  await page.goto('/?incident=progress-incident&view=mindmap');
  await expect(page.getByText('1 of 2 source batches complete')).toBeVisible();
  await expect(page.locator('.board-build-track')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Build mind map' })).toHaveCount(0);
  ready = true;
  await expect(page.getByText('Ready to replay', { exact: true })).toBeVisible();
  await expect(page.locator('.board-build-track')).toHaveCount(0);
  expect(builds).toBe(0);
});

test('completed transcripts start generation without a click', async ({ page }) => {
  let builds = 0;
  let force = false;
  let state = 'not_built';
  const playback = { run_id: 'ready-run', state: 'ended', position_seconds: 20, duration_seconds: 20,
    revision: 0, server_time: new Date().toISOString() };
  const incident = { id: 'ready-incident', title: 'Ready board', recordings: [], playback };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/knowledge') && route.request().method() === 'POST') {
      builds++;
      force = new URL(route.request().url()).searchParams.get('force') === 'true';
      state = 'processing';
      await route.fulfill({ json: { id: 'version', status: 'queued' } });
      return;
    }
    await route.fulfill({ json: path.endsWith('/knowledge') ? {
      run_id: playback.run_id, ready: true, state, completed: 2, expected: 2, failed: 0, reason: '',
      stale: false, version_id: null, nodes: [], edges: [],
      progress: { stage: state === 'processing' ? 'analyzing' : 'waiting', completed_batches: 0, total_batches: 0 },
    } : path.endsWith('/playback') ? playback : incident });
  });
  await page.goto('/?incident=ready-incident&view=mindmap');
  await expect.poll(() => builds).toBe(1);
  expect(force).toBe(false);
  await expect(page.getByText('Connecting source-backed observations. You can keep reviewing the incident.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Skip waiting and start generation' })).toHaveCount(0);
});

test('skip waiting starts generation before every transcript finishes', async ({ page }) => {
  let builds = 0;
  let force = false;
  let state = 'not_built';
  const playback = { run_id: 'skip-run', state: 'ended', position_seconds: 20, duration_seconds: 20,
    revision: 0, server_time: new Date().toISOString() };
  const incident = { id: 'skip-incident', title: 'Partial transcripts', recordings: [], playback };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/knowledge') && route.request().method() === 'POST') {
      builds++;
      force = new URL(route.request().url()).searchParams.get('force') === 'true';
      state = 'processing';
      await route.fulfill({ json: { id: 'version', status: 'queued' } });
      return;
    }
    await route.fulfill({ json: path.endsWith('/knowledge') ? {
      run_id: playback.run_id, ready: false, state, completed: 1, expected: 2, failed: 0,
      reason: 'Waiting for transcripts: 1/2 complete; 0 failed.', stale: false, version_id: null, nodes: [], edges: [],
      progress: { stage: 'waiting', completed_batches: 0, total_batches: 0 },
    } : path.endsWith('/playback') ? playback : incident });
  });
  await page.goto('/?incident=skip-incident&view=mindmap');
  await expect(page.getByText('Waiting for transcripts: 1/2 complete; 0 failed.')).toBeVisible();
  await expect(page.getByText('1 of 2 transcript windows complete')).toBeVisible();
  await page.getByRole('button', { name: 'Skip waiting and start generation' }).click();
  await expect.poll(() => builds).toBe(1);
  expect(force).toBe(true);
});

test('failed build keeps a single retry action in the waiting card', async ({ page }) => {
  let builds = 0;
  let force = false;
  let state = 'failed';
  const playback = { run_id: 'failed-run', state: 'ended', position_seconds: 20, duration_seconds: 20,
    revision: 0, server_time: new Date().toISOString() };
  const incident = { id: 'failed-incident', title: 'Failed board', recordings: [], playback };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/knowledge') && route.request().method() === 'POST') {
      builds++;
      force = new URL(route.request().url()).searchParams.get('force') === 'true';
      state = 'processing';
      await route.fulfill({ json: { id: 'version', status: 'queued' } });
      return;
    }
    await route.fulfill({ json: path.endsWith('/knowledge') ? {
      run_id: playback.run_id, ready: true, state, completed: 2, expected: 2, failed: 0, reason: '',
      error: state === 'failed' ? 'Map build failed validation or its inputs changed. Saved maps are retained. Retry the build.' : undefined,
      stale: false, version_id: null, nodes: [], edges: [],
      progress: { stage: state === 'processing' ? 'analyzing' : 'failed', completed_batches: 0, total_batches: 0 },
    } : path.endsWith('/playback') ? playback : incident });
  });
  await page.goto('/?incident=failed-incident&view=mindmap');
  await expect(page.getByRole('button', { name: 'Retry map build' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Start generation now' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Retry map build' }).click();
  await expect.poll(() => builds).toBe(1);
  expect(force).toBe(false);
});

test('knowledge map shows people and connections, filters future statements and seeks the shared incident', async ({ page }) => {
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
    if (path.endsWith('/playback') && route.request().method() === 'POST') {
      const command = route.request().postDataJSON();
      if (command.action === 'seek') { commands++; playback.position_seconds = command.position_seconds; playback.revision++; }
    }
    const body = path.endsWith('/knowledge') ? graph : path.endsWith('/events') ? {
      run_id: playback.run_id, configured: true, state: 'idle', events: [], pending_segments: 0, processed_segments: 3,
    } : path.endsWith('/transcripts') ? { run_id: playback.run_id, recordings: [] }
      : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=knowledge-ui');
  await expect(page.getByRole('region', { name: 'Incident map', exact: true })).toHaveCount(0);
  const opened = page.waitForEvent('popup');
  await page.getByRole('link', { name: 'Open mind map' }).click();
  const mapWindow = await opened;
  const map = mapWindow.locator('.knowledge-map');
  await expect(mapWindow).toHaveURL(/view=mindmap/);
  await expect(mapWindow.getByText('Independent map window')).toBeVisible();
  await expect(mapWindow.getByRole('region', { name: 'Incident map', exact: true })).toHaveCount(0);
  await expect(map.getByRole('button', { name: 'Play reconstruction', exact: true })).toBeVisible();
  await expect(map.locator('.graph-node')).toHaveCount(0);
  await map.getByRole('button', { name: 'Play reconstruction', exact: true }).click();
  await expect.poll(() => map.locator('.graph-node').count()).toBeGreaterThan(0);
  expect(commands).toBe(0);
  await map.getByRole('button', { name: 'Pause reconstruction', exact: true }).click();
  await expect(map.locator('.knowledge-contradicted')).toHaveCount(0);
  await map.getByRole('button', { name: 'Final board', exact: true }).click();
  await expect(map.locator('.knowledge-contradicted')).toHaveCount(1);
  await map.getByRole('checkbox', { name: 'Follow timeline' }).check();
  await expect(map.getByText('Correction: phone', { exact: true })).toHaveCount(0);
  await expect(map.locator('.knowledge-contradicted')).toHaveCount(0);
  await map.getByRole('checkbox', { name: 'Follow timeline' }).uncheck();
  await map.getByRole('button', { name: 'Connection: Witness speaks with responding officer' }).focus();
  await mapWindow.keyboard.press('Enter');
  const inspector = map.getByRole('complementary', { name: 'Map details' });
  await expect(inspector.getByRole('heading', { name: 'Witness speaks with responding officer' })).toBeVisible();
  await inspector.getByRole('button', { name: /Jump to incident at/ }).click();
  await expect(mapWindow.getByText('Incident replay moved to the selected source time.')).toBeVisible();
  await expect(mapWindow.locator('video')).toHaveCount(0);
  expect(commands).toBe(1);
  await map.getByRole('button', { name: 'Close details' }).click();
  const witness = map.getByRole('button', { name: 'Unidentified witness, person, active' });
  await witness.click();
  await expect(inspector.getByRole('heading', { name: 'Unidentified witness' })).toBeVisible();
  await map.getByRole('checkbox', { name: 'Focus connections' }).check();
  await expect(map.locator('.graph-node')).toHaveCount(2);
  await map.getByRole('checkbox', { name: 'Focus connections' }).uncheck();
  await map.getByRole('button', { name: 'Close details' }).click();
  await map.getByLabel('Find in knowledge map').fill('witness');
  await expect(map.locator('.graph-count')).toContainText('1 matches');
  await map.getByLabel('Find in knowledge map').fill('');
  await map.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect(map.getByLabel('Graph zoom')).toHaveText('120%');
  await map.getByRole('button', { name: 'Reset view' }).click();
  const before = await witness.getAttribute('transform');
  const dot = await witness.locator('.node-dot').boundingBox();
  if (!dot) throw new Error('Missing graph node');
  await mapWindow.mouse.move(dot.x + dot.width / 2, dot.y + dot.height / 2);
  await mapWindow.mouse.down();
  await mapWindow.mouse.move(dot.x + 70, dot.y + 50, { steps: 8 });
  await mapWindow.mouse.up();
  await expect(witness).not.toHaveAttribute('transform', before!);
  expect(await mapWindow.evaluate(() => Object.keys(JSON.parse(localStorage.getItem('bop:board-layout:knowledge-ui:version') || '{}')))).toContain('person');
  await map.getByRole('button', { name: 'Reset view' }).click();
  await witness.focus();
  await mapWindow.keyboard.press('Enter');
  await expect(inspector.getByRole('heading', { name: 'Unidentified witness' })).toBeVisible();
  await mapWindow.screenshot({ path: 'test-results/knowledge-map-window.png', fullPage: true });
  await mapWindow.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => mapWindow.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await mapWindow.screenshot({ path: 'test-results/knowledge-map-mobile.png', fullPage: true });
});
