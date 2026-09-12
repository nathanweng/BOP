import { test, expect } from './fixtures';
import { readFileSync } from 'node:fs';

test('facts filter, show corrections, and seek existing cameras on the shared timeline', async ({ page, mp4Path }) => {
  let corrected = false;
  const commands: number[] = [];
  const playback = { run_id: 'history-run', state: 'paused', position_seconds: 0, duration_seconds: 14,
    revision: 0, server_time: new Date().toISOString() };
  const recording = { id: 'camera', camera_label: 'Camera A', original_filename: 'source.mp4', duration_seconds: 12,
    start_offset_seconds: 2, size_bytes: 100, media_url: '/fixture-source.mp4', validation_status: 'ready',
    processing_status: 'transcribing', latest_analyzed_time_seconds: 12 };
  const incident = { id: 'history-ui', title: 'Incident review', context: 'Witness reports and responding officer observations.',
    created_at: new Date().toISOString(), recordings: [recording], playback };
  const media = readFileSync(mp4Path);
  await page.route('**/fixture-source.mp4', (route) => {
    const range = route.request().headers().range?.match(/bytes=(\d+)-(\d*)/);
    const start = range ? Number(range[1]) : 0;
    const end = range?.[2] ? Number(range[2]) : media.length - 1;
    return route.fulfill({ status: range ? 206 : 200, body: media.subarray(start, end + 1), contentType: 'video/mp4',
      headers: { 'Accept-Ranges': 'bytes', 'Content-Length': String(end - start + 1), ...(range ? { 'Content-Range': 'bytes ' + start + '-' + end + '/' + media.length } : {}) } });
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/playback') && route.request().method() === 'POST') {
      const command = route.request().postDataJSON();
      if (command.action === 'seek') { commands.push(command.position_seconds); playback.position_seconds = command.position_seconds; playback.revision++; }
    }
    const events = [
      { id: 'one', segment_id: 'first', recording_id: 'camera', timestamp_seconds: 2, local_seconds: 0,
        title: 'Witness reports an object in the subject’s hand', status: corrected ? 'disproven' : 'current',
        status_timestamp_seconds: corrected ? 10 : undefined, status_recording_id: corrected ? 'camera' : undefined,
        status_reason: corrected ? 'Witness identifies the object as a phone.' : undefined },
      { id: 'two', segment_id: 'later', recording_id: 'camera', timestamp_seconds: 6, local_seconds: 4,
        title: 'Officer reports the building is empty', status: corrected ? 'outdated' : 'current',
        status_timestamp_seconds: corrected ? 12 : undefined, status_recording_id: corrected ? 'camera' : undefined,
        status_reason: corrected ? 'Upstairs has not been checked.' : undefined },
      { id: 'three', segment_id: 'third', recording_id: 'camera', timestamp_seconds: 8, local_seconds: 6,
        title: 'Responding officer establishes contact with the witness', status: 'current' },
    ];
    const body = path.endsWith('/events') ? { run_id: playback.run_id, configured: true,
      model: 'test', state: 'idle', pending_segments: 0, processed_segments: corrected ? 3 : 1, error: null, events }
      : path.endsWith('/transcripts') ? { run_id: playback.run_id, transcription_configured: true, recordings: [] }
      : path.endsWith('/playback') ? playback : path === '/api/incidents' ? [incident] : incident;
    await route.fulfill({ json: body });
  });
  await page.goto('/?incident=history-ui');
  const facts = page.locator('#event-history');
  const log = facts.getByRole('list', { name: 'Event log' });
  await expect(log).toBeVisible();
  await expect(log.getByRole('listitem')).toHaveCount(3);
  await expect(facts.locator('.event-current')).toHaveCount(3);
  await facts.getByRole('button', { name: 'Jump to incident at 00:06.0' }).click();
  await expect(page.getByTestId('incident-clock')).toHaveAttribute('data-seconds', '6.000');
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(4, 1);
  await expect(page.locator('video')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Evidence', exact: true })).toHaveCount(0);
  corrected = true;
  await expect(log.getByRole('listitem')).toHaveCount(3);
  await expect(facts.locator('.event-disproven')).toHaveCount(1);
  await expect(facts.locator('.event-outdated')).toHaveCount(1);
  await expect(facts.locator('.event-current')).toHaveCount(1);
  await expect(log.getByRole('listitem').first()).toContainText('Witness reports an object');
  await expect(log.getByRole('listitem').nth(1)).toContainText('Officer reports the building is empty');
  await expect(facts.getByText('Upstairs has not been checked.')).toBeVisible();
  await facts.getByRole('button', { name: 'Jump to update at 00:12.0' }).click();
  await expect(page.getByTestId('incident-clock')).toHaveAttribute('data-seconds', '12.000');
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(10, 1);
  expect(commands).toEqual([6, 12]);
  await facts.getByRole('button', { name: 'Needs review 2', exact: true }).click();
  await expect(log.getByRole('listitem')).toHaveCount(2);
  await expect(facts.locator('.event-current')).toHaveCount(0);
  await facts.getByRole('button', { name: 'All events 3', exact: true }).click();
  await expect(log.getByRole('listitem')).toHaveCount(3);
  await facts.scrollIntoViewIfNeeded();
  await facts.screenshot({ path: 'test-results/events-facts-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await facts.scrollIntoViewIfNeeded();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await facts.screenshot({ path: 'test-results/events-facts-mobile.png' });
});
