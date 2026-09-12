import { apiURL, createIncident, expect, readPlaybackSample, test, uploadRecording } from './fixtures';

interface IncidentDetail {
  id: string;
  title: string;
  context: string;
  recordings: Array<{
    id: string;
    camera_label: string;
    start_offset_seconds: number;
    duration_seconds: number;
    validation_status: string;
    processing_status: string;
    latest_analyzed_time_seconds: number | null;
    size_bytes: number;
  }>;
  playback: {
    run_id: string;
    state: string;
    position_seconds: number;
    revision: number;
    speed?: number;
  };
}

test('real uploads, alignment and shared replay persist across reload and restart', async ({ page, request, mp4Path }) => {
  const incident = await createIncident(page, 'synchronized replay');
  const play = page.getByRole('button', { name: 'Play', exact: true });
  await expect(play).toBeDisabled();

  const cameraA = await uploadRecording(page, incident.id, mp4Path, 'Camera A', 0);
  await expect(play).toBeEnabled();
  const cameraB = await uploadRecording(page, incident.id, mp4Path, 'Camera B', 2);
  await expect(play).toBeEnabled();
  expect(cameraA.duration_seconds).toBeCloseTo(12, 0);
  expect(cameraB.duration_seconds).toBeCloseTo(12, 0);

  const readIncident = async () => {
    const response = await request.get(`${apiURL}/api/incidents/${incident.id}`);
    expect(response.ok(), await response.text()).toBeTruthy();
    return await response.json() as IncidentDetail;
  };

  await test.step('persist the real uploads and their alignment', async () => {
    await page.reload();
    await expect(play).toBeEnabled();
    const stored = await readIncident();
    expect(stored.title).toBe(incident.title);
    expect(stored.context).toBe('Disposable playback verification.');
    expect(stored.recordings).toHaveLength(2);
    expect(stored.recordings.find((recording) => recording.id === cameraA.id)).toMatchObject({
      camera_label: 'Camera A', start_offset_seconds: 0, validation_status: 'ready',
    });
    expect(stored.recordings.find((recording) => recording.id === cameraB.id)).toMatchObject({
      camera_label: 'Camera B', start_offset_seconds: 2, validation_status: 'ready',
    });
    for (const recording of stored.recordings) expect(recording.size_bytes).toBeGreaterThan(1000);
    await expect(page.getByLabel('Start offset for Camera B (seconds)', { exact: true })).toHaveValue('2');
    await expect.poll(async () => {
      const sample = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
      return sample.videos.every((video) => video.readyState >= 1 && !video.error);
    }).toBe(true);
  });

  const initialRun = (await readIncident()).playback.run_id;

  await test.step('keep the second feed stopped until its incident start offset', async () => {
    await play.click();
    await expect.poll(async () => (await readPlaybackSample(page, [cameraA.id])).incidentTime).toBeGreaterThan(0.2);
    const early = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
    expect(early.incidentTime).toBeLessThan(2);
    expect(early.videos[0].paused).toBe(false);
    expect(early.videos[1].paused).toBe(true);
    expect(early.videos[1].currentTime).toBeLessThan(0.1);
    await expect(page.getByTestId(`feed-status-${cameraB.id}`)).toHaveText('Not started');
  });

  await test.step('map each actual video time to the shared incident clock', async () => {
    await expect.poll(async () => {
      const sample = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
      return {
        bothStarted: sample.incidentTime >= 3,
        bothPlaying: sample.videos.every((video) => !video.paused && video.readyState >= 2 && !video.error),
        cameraAligned: Math.abs(sample.videos[0].currentTime - sample.incidentTime) < 0.35,
        delayedCameraAligned: Math.abs(sample.videos[1].currentTime - (sample.incidentTime - 2)) < 0.35,
      };
    }).toEqual({ bothStarted: true, bothPlaying: true, cameraAligned: true, delayedCameraAligned: true });
    await expect(page.getByTestId(`feed-status-${cameraB.id}`)).toHaveText('Playing');
  });

  let pausedTime = 0;
  await test.step('pause freezes the incident clock and both video elements', async () => {
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await expect(play).toBeEnabled();
    await expect.poll(async () => (await readPlaybackSample(page, [cameraA.id, cameraB.id])).videos.every((video) => video.paused)).toBe(true);
    const before = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
    // An elapsed interval is intentional: it verifies time does not advance while paused.
    await page.waitForTimeout(750);
    const after = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
    expect(Math.abs(after.incidentTime - before.incidentTime)).toBeLessThan(0.02);
    for (let index = 0; index < after.videos.length; index += 1) {
      expect(Math.abs(after.videos[index].currentTime - before.videos[index].currentTime)).toBeLessThan(0.05);
    }
    pausedTime = after.incidentTime;
    expect(pausedTime).toBeGreaterThan(2);
    const persisted = (await readIncident()).playback;
    expect(persisted.state).toBe('paused');
    expect(Math.abs(persisted.position_seconds - pausedTime)).toBeLessThan(0.1);
  });

  await test.step('reload restores the paused cutoff and synchronized video positions', async () => {
    await page.reload();
    await expect(play).toBeEnabled();
    await expect.poll(async () => {
      const sample = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
      return {
        clockRestored: Math.abs(sample.incidentTime - pausedTime) < 0.1,
        allPaused: sample.videos.every((video) => video.paused && !video.error),
        firstRestored: Math.abs(sample.videos[0].currentTime - pausedTime) < 0.25,
        secondRestored: Math.abs(sample.videos[1].currentTime - (pausedTime - 2)) < 0.25,
      };
    }).toEqual({ clockRestored: true, allPaused: true, firstRestored: true, secondRestored: true });
  });

  await test.step('resume continues the same run from the saved cutoff', async () => {
    await play.click();
    await expect.poll(async () => (await readPlaybackSample(page, [cameraA.id])).incidentTime).toBeGreaterThan(pausedTime + 0.4);
    expect((await readIncident()).playback.run_id).toBe(initialRun);
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await expect(play).toBeEnabled();
  });

  await test.step('reset to 0 keeps the same run so transcripts and events survive', async () => {
    await page.getByTestId('reset-to-start').click();
    await expect.poll(async () => (await readPlaybackSample(page, [cameraA.id, cameraB.id])).incidentTime).toBe(0);
    const afterReset = (await readIncident()).playback;
    expect(afterReset.run_id).toBe(initialRun);
    expect(afterReset.state).toBe('paused');
    expect(afterReset.position_seconds).toBe(0);
    await expect.poll(async () => {
      const sample = await readPlaybackSample(page, [cameraA.id, cameraB.id]);
      return sample.videos.every((video) => video.paused && video.currentTime < 0.1);
    }).toBe(true);
  });

  await test.step('clear all history creates a new durable run after confirmation', async () => {
    await page.getByTestId('clear-history').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByTestId('confirm-clear').click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect.poll(async () => (await readIncident()).playback.run_id).not.toBe(initialRun);
    const restarted = (await readIncident()).playback;
    expect(restarted.state).toBe('paused');
    expect(restarted.position_seconds).toBe(0);
    await page.reload();
    await expect(play).toBeEnabled();
    expect((await readIncident()).playback.run_id).toBe(restarted.run_id);
    await expect(page.getByTestId('incident-clock')).toHaveAttribute('data-seconds', '0.000');
  });

  await test.step('manual alignment edits survive another reload', async () => {
    await page.locator('.setup-panel > summary').click();
    const alignment = page.getByRole('form', { name: 'Alignment for Camera B', exact: true });
    await alignment.getByLabel('Camera label for Camera B', { exact: true }).fill('Camera B aligned');
    await alignment.getByLabel('Start offset for Camera B (seconds)', { exact: true }).fill('3');
    const saved = page.waitForResponse((response) =>
      new URL(response.url()).pathname === `/api/incidents/${incident.id}/recordings/${cameraB.id}` &&
      response.request().method() === 'PATCH',
    );
    await alignment.getByRole('button', { name: 'Save alignment', exact: true }).click();
    const response = await saved;
    expect(response.ok(), await response.text()).toBeTruthy();
    await page.reload();
    await page.locator('.setup-panel > summary').click();
    await expect(page.getByLabel('Start offset for Camera B aligned (seconds)', { exact: true })).toHaveValue('3');
    expect((await readIncident()).recordings.find((recording) => recording.id === cameraB.id)).toMatchObject({
      camera_label: 'Camera B aligned', start_offset_seconds: 3,
    });
  });

  await test.step('scene summary stays empty until analyzed events exist', async () => {
    await expect(page.getByRole('heading', { name: '02 Scene summary', exact: true })).toBeVisible();
    await expect(page.getByTestId('analysis-empty-state')).toContainText(/Awaiting analyzed events|Scene summaries are unavailable/);
    await expect(page.getByTestId('reconstruction-empty-state')).toHaveCount(0);
    const stored = await readIncident();
    for (const recording of stored.recordings) {
      expect(recording.processing_status).toBe('transcribing');
    }
  });
});

test('a finished feed stops while the later feed continues, then the shared run ends', async ({ page, request, mp4Path }) => {
  const incident = await createIncident(page, 'natural completion');
  const first = await uploadRecording(page, incident.id, mp4Path, 'Early camera', 0);
  const second = await uploadRecording(page, incident.id, mp4Path, 'Later camera', 2);
  await page.getByRole('button', { name: 'Play', exact: true }).click();

  await expect.poll(async () => {
    const sample = await readPlaybackSample(page, [first.id, second.id]);
    return {
      firstEnded: sample.incidentTime >= first.duration_seconds && sample.videos[0].paused,
      secondStillPlaying: !sample.videos[1].paused && sample.incidentTime < second.duration_seconds + 2,
      secondAligned: Math.abs(sample.videos[1].currentTime - (sample.incidentTime - 2)) < 0.35,
    };
  }, { timeout: 16_000, intervals: [100] }).toEqual({ firstEnded: true, secondStillPlaying: true, secondAligned: true });
  await expect(page.getByTestId(`feed-status-${first.id}`)).toHaveText('Ended');
  await expect(page.getByTestId(`feed-status-${second.id}`)).toHaveText('Playing');

  await expect(page.getByTestId(`feed-status-${second.id}`)).toHaveText('Ended', { timeout: 5000 });
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeDisabled();
  const final = await readPlaybackSample(page, [first.id, second.id]);
  expect(final.incidentTime).toBeCloseTo(second.duration_seconds + 2, 2);
  expect(final.videos.every((video) => video.paused)).toBe(true);

  const response = await request.get(`${apiURL}/api/incidents/${incident.id}`);
  expect(response.ok()).toBeTruthy();
  const stored = await response.json() as IncidentDetail;
  expect(stored.playback.state).toBe('ended');
  expect(stored.playback.position_seconds).toBeCloseTo(second.duration_seconds + 2, 2);
  await page.reload();
  await expect(page.getByTestId(`feed-status-${second.id}`)).toHaveText('Ended');
  await expect(page.getByTestId('reset-to-start')).toBeEnabled();
  await expect(page.getByTestId('clear-history')).toBeEnabled();
});

test('unreadable MP4 gives an actionable upload error without creating a recording', async ({ page, request }) => {
  const incident = await createIncident(page, 'invalid upload', '');
  await page.getByLabel('Camera label', { exact: true }).fill('Unreadable camera');
  await page.getByLabel('MP4 recording', { exact: true }).setInputFiles({
    name: 'unreadable.mp4', mimeType: 'video/mp4', buffer: Buffer.from('This is not an MP4 recording.'),
  });
  const rejected = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/incidents/${incident.id}/recordings` &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Upload recording', exact: true }).click();
  const response = await rejected;
  expect(response.status()).toBe(422);
  const error = await response.json() as { detail: string };
  expect(error.detail).toMatch(/MP4|video|recording|media/i);
  await expect(page.getByRole('alert').filter({ hasText: error.detail })).toBeVisible();
  const detail = await request.get(`${apiURL}/api/incidents/${incident.id}`);
  expect(detail.ok()).toBeTruthy();
  const stored = await detail.json() as IncidentDetail;
  expect(stored.context).toBe('');
  expect(stored.recordings).toEqual([]);
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
});

test('a lost clock connection freezes local playback and recovery confirms server state', async ({ page, request, mp4Path }) => {
  const incident = await createIncident(page, 'clock connection loss');
  const first = await uploadRecording(page, incident.id, mp4Path, 'Connection camera A', 0);
  const second = await uploadRecording(page, incident.id, mp4Path, 'Connection camera B', 0);
  const playbackURL = `${apiURL}/api/incidents/${incident.id}/playback`;
  const browserPlaybackRoute = `**/api/incidents/${incident.id}/playback`;

  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(async () => (await readPlaybackSample(page, [first.id])).incidentTime).toBeGreaterThan(0.4);
  await page.route(browserPlaybackRoute, (route) => route.abort('failed'));
  try {
    await expect(page.getByText('Replay connection lost. Videos are paused locally. Reconnect before continuing.', { exact: true })).toBeVisible();
    await expect(page.getByText(/Could not confirm that the server paused/)).toBeVisible();
    await expect.poll(async () => {
      const sample = await readPlaybackSample(page, [first.id, second.id]);
      return sample.videos.every((video) => video.paused && Math.abs(video.currentTime - sample.incidentTime) < 0.1);
    }).toBe(true);
    const before = await readPlaybackSample(page, [first.id, second.id]);
    await page.waitForTimeout(750);
    const after = await readPlaybackSample(page, [first.id, second.id]);
    expect(Math.abs(after.incidentTime - before.incidentTime)).toBeLessThan(0.02);
    for (let index = 0; index < after.videos.length; index += 1) {
      expect(Math.abs(after.videos[index].currentTime - before.videos[index].currentTime)).toBeLessThan(0.05);
    }
    await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
  } finally {
    await page.unroute(browserPlaybackRoute);
  }

  // Transport failures affect the browser, not the independent server clock.
  // On recovery, the UI must reconcile and confirm a server pause before resuming.
  await expect.poll(async () => {
    const response = await request.get(playbackURL);
    return (await response.json() as IncidentDetail['playback']).state;
  }).toBe('paused');
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
  const recovered = await request.get(playbackURL);
  const recoveredPlayback = await recovered.json() as IncidentDetail['playback'];
  await expect.poll(async () => {
    const sample = await readPlaybackSample(page, [first.id, second.id]);
    return Math.abs(sample.incidentTime - recoveredPlayback.position_seconds) < 0.1;
  }).toBe(true);
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(async () => (await readPlaybackSample(page, [first.id])).incidentTime).toBeGreaterThan(recoveredPlayback.position_seconds + 0.3);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
});

test('demo replay speeds advance the shared clock and video rate', async ({ page, request, mp4Path }) => {
  const incident = await createIncident(page, 'demo speed');
  const camera = await uploadRecording(page, incident.id, mp4Path, 'Camera A', 0);
  await expect(page.getByRole('button', { name: '1×', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(async () => (await readPlaybackSample(page, [camera.id])).videos[0].playbackRate).toBe(1);
  await page.getByRole('button', { name: '4×', exact: true }).click();
  await expect(page.getByRole('button', { name: '4×', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const before = await readPlaybackSample(page, [camera.id]);
  await expect.poll(async () => {
    const sample = await readPlaybackSample(page, [camera.id]);
    return sample.videos[0].playbackRate === 4 && sample.incidentTime > before.incidentTime + 0.6;
  }).toBe(true);
  const stored = await request.get(`${apiURL}/api/incidents/${incident.id}`);
  expect((await stored.json() as IncidentDetail).playback.speed).toBe(4);
});

test('an incident can be removed from the library', async ({ page, request }) => {
  const incident = await createIncident(page, 'removable incident');
  await page.getByRole('link', { name: 'All incidents' }).click();
  await expect(page.getByRole('heading', { name: /Incidents/ })).toBeVisible();
  await expect(page.getByRole('button', { name: new RegExp(incident.title) })).toBeVisible();
  await page.getByRole('button', { name: `Remove ${incident.title}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Remove this incident?' })).toBeVisible();
  const deleted = page.waitForResponse(
    (response) => new URL(response.url()).pathname === `/api/incidents/${incident.id}` &&
      response.request().method() === 'DELETE',
  );
  await page.getByRole('button', { name: 'Remove incident', exact: true }).click();
  const deletedResponse = await deleted;
  expect(deletedResponse.ok(), await deletedResponse.text()).toBeTruthy();
  await expect(page.getByRole('button', { name: new RegExp(incident.title) })).toHaveCount(0);
  expect((await request.get(`${apiURL}/api/incidents/${incident.id}`)).status()).toBe(404);
});
