import { describe, expect, it } from 'vitest';
import { buildCallSitrep } from './callSitrep';

describe('911 call sitrep extraction', () => {
  it('extracts location, caller, and situation details from a 911 transcript', () => {
    const sitrep = buildCallSitrep(
      [{
        id: 'rec-1',
        original_filename: '911-call.mp3',
        camera_label: '911 Call',
        duration_seconds: 45,
        start_offset_seconds: 0,
        size_bytes: 2048,
        media_url: '/api/recordings/1/media',
        validation_status: 'ready',
        processing_status: 'transcribing',
        latest_analyzed_time_seconds: null,
      }],
      {
        run_id: 'run-1',
        incident_position_seconds: 0,
        transcription_configured: true,
        recordings: [{
          recording_id: 'rec-1',
          latest_analyzed_time_seconds: 45,
          segments: [{
            id: 'seg-1',
            recording_id: 'rec-1',
            run_id: 'run-1',
            local_start_seconds: 0,
            local_end_seconds: 20,
            incident_start_seconds: 0,
            incident_end_seconds: 20,
            status: 'completed',
            text: 'This is Maria Lopez. I am at 123 Main Street, apartment 3B. There is a man with a gun outside the building. Please send police and EMS right away.',
            error: null,
            turns: [{ speaker: 1, label: 'Caller', text: 'This is Maria Lopez. I am at 123 Main Street, apartment 3B. There is a man with a gun outside the building.', local_start_seconds: 0, local_end_seconds: 10 }],
          }],
        }],
      },
    );

    expect(sitrep).not.toBeNull();
    expect(sitrep?.location).toContain('123 Main Street');
    expect(sitrep?.caller).toMatch(/Maria Lopez/i);
    expect(sitrep?.situation).toMatch(/gun|armed|police|ems/i);
  });
});
