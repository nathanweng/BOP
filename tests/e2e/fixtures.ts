import { test as base, expect, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import ffmpegPath from 'ffmpeg-static';

export const apiURL = process.env.E2E_API_URL ?? 'http://localhost:8000';

// Each worker creates and removes only its own temporary media directory.
// No database records are removed: the suite can run against a developer's stack.
export const test = base.extend<{}, { mp4Path: string }>({
  mp4Path: [
    async ({}, use) => {
      const fixtureDirectory = mkdtempSync(join(tmpdir(), 'bop-e2e-'));
      const mp4Path = join(fixtureDirectory, 'camera-fixture.mp4');
      try {
        const result = spawnSync(
          process.env.FFMPEG_BINARY ?? ffmpegPath ?? 'ffmpeg',
          [
            '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
            '-t', '12', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart',
            mp4Path,
          ],
          { encoding: 'utf8', timeout: 30_000 },
        );
        if (result.error || result.status !== 0) {
          throw new Error(
            'E2E media generation failed. Install FFmpeg or set FFMPEG_BINARY ' +
            `to an executable with libx264 support. ${result.error?.message ?? result.stderr}`,
          );
        }
        await use(mp4Path);
      } finally {
        rmSync(fixtureDirectory, { recursive: true, force: true });
      }
    },
    { scope: 'worker' },
  ],
});

export { expect };

export async function createIncident(page: Page, prefix: string, context = 'Disposable playback verification.') {
  const title = `E2E ${prefix} ${randomUUID()}`;
  await page.goto('/');
  await page.getByRole('button', { name: 'New incident', exact: true }).click();
  await page.getByLabel('Incident title', { exact: true }).fill(title);
  await page.getByLabel(/^Incident context/).fill(context);
  const created = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/incidents' &&
      response.request().method() === 'POST',
  );
  await page.getByRole('form', { name: 'Create incident', exact: true }).getByRole('button', { name: 'Create incident', exact: true }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  const incident = await response.json() as { id: string };
  await expect(page).toHaveURL(new RegExp(`[?&]incident=${incident.id}(?:&|$)`));
  return { id: incident.id, title };
}

export async function uploadRecording(page: Page, incidentId: string, mp4Path: string, label: string, offset: number) {
  if (!(await page.getByLabel('Camera label', { exact: true }).isVisible())) {
    await page.locator('.setup-panel > summary').click();
  }
  await page.getByLabel('Camera label', { exact: true }).fill(label);
  await page.getByLabel('Start offset (seconds)', { exact: true }).fill(String(offset));
  await page.getByLabel('MP4 recording', { exact: true }).setInputFiles(mp4Path);
  const uploaded = page.waitForResponse(
    (response) => new URL(response.url()).pathname === `/api/incidents/${incidentId}/recordings` &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Upload recording', exact: true }).click();
  const response = await uploaded;
  expect(response.ok(), await response.text()).toBeTruthy();
  const recording = await response.json() as { id: string; duration_seconds: number };
  await expect(page.getByTestId(`camera-feed-${recording.id}`)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Upload recording', exact: true })).toBeEnabled();
  return recording;
}

export async function readPlaybackSample(page: Page, recordingIds: string[]) {
  return await page.evaluate((ids) => {
    const clock = document.querySelector<HTMLElement>('[data-testid="incident-clock"]');
    if (!clock?.dataset.seconds) throw new Error('The incident clock has no numeric data-seconds value.');
    return {
      incidentTime: Number(clock.dataset.seconds),
      videos: ids.map((id) => {
        const video = document.querySelector<HTMLVideoElement>(`[data-testid="camera-feed-${id}"] video`);
        if (!video) throw new Error(`No video element for recording ${id}.`);
        return { currentTime: video.currentTime, paused: video.paused, playbackRate: video.playbackRate, readyState: video.readyState, error: video.error?.message ?? null };
      }),
    };
  }, recordingIds);
}
