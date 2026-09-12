from dataclasses import replace
import json

import pytest

from bop_api.models import Incident, PlaybackRun, Recording, TranscriptSegment, EventHistory, SituationReportVersion
from bop_api.situation import SituationService, validate_report


def report(ref='s0'):
    return {'overview': [{'text': 'A witness reports an incident at the entrance.', 'source_ids': [ref]}],
            'developments': [], 'scene_status': [],
            'clarifications': [{'text': 'Which entrance does the witness mean?', 'source_ids': [ref]}]}


@pytest.fixture
def prepared(app, incident, clock):
    run_id = incident['playback']['run_id']
    with app.state.sessions() as session:
        session.add(Recording(id='cam', incident_id=incident['id'], original_filename='test.mp4',
            camera_label='Camera A', storage_key='sitrep-test.mp4', duration_seconds=60,
            start_offset_seconds=0, size_bytes=100, created_at=clock()))
        session.get(PlaybackRun, run_id).position_seconds = 30
        session.commit()
        session.add(TranscriptSegment(id='s0', run_id=run_id, recording_id='cam',
            local_start_seconds=0, local_end_seconds=10, incident_start_seconds=0,
            incident_end_seconds=10, status='completed', text='I saw it at the entrance.', created_at=clock()))
        session.add(EventHistory(run_id=run_id, processed_json='["s0"]', events_json=json.dumps([
            {'id': 'event1', 'segment_id': 's0', 'recording_id': 'cam', 'timestamp_seconds': 0,
             'local_seconds': 0, 'title': 'Witness reports an incident at an entrance', 'status': 'current'}])))
        session.commit()
    service = SituationService(replace(app.state.settings, openrouter_api_key='test'), app.state.sessions, clock,
                               lambda *_: report())
    return service, incident['id'], run_id


def view(app, prepared):
    service, iid, rid = prepared
    with app.state.sessions() as session:
        return service.view(session, session.get(Incident, iid), session.get(PlaybackRun, rid))


def revise(app, prepared, clock):
    clock.advance(1)
    with app.state.sessions() as session:
        session.add(TranscriptSegment(id='s20', run_id=prepared[2], recording_id='cam',
            local_start_seconds=20, local_end_seconds=30, incident_start_seconds=20,
            incident_end_seconds=30, status='completed', text='I meant the rear entrance, not the front.', created_at=clock()))
        history = session.get(EventHistory, prepared[2])
        events = json.loads(history.events_json)
        events[0].update(status='outdated', status_reason='Entrance clarified',
            status_recording_id='cam', status_local_seconds=20, status_timestamp_seconds=20,
            assessment_history=[{'segment_id': 's20', 'status': 'outdated', 'reason': 'Entrance clarified'}])
        history.events_json, history.processed_json = json.dumps(events), '["s0", "s20"]'
        session.commit()


def test_persisted_briefing_is_cited_and_unchanged_inputs_do_not_repeat_calls(app, prepared):
    service, iid, _ = prepared
    service.process(iid)
    data = view(app, prepared)
    assert data['state'] == 'completed' and data['known_through'] == 10
    assert data['report']['sources']['s0']['text'] == 'I saw it at the entrance.'
    service.generator = lambda *_: pytest.fail('Must reuse the saved briefing')
    service.process(iid)
    assert view(app, prepared)['version_id'] == data['version_id']


def test_corrections_update_report_and_rewind_restores_earlier_version(app, prepared, clock):
    service, iid, rid = prepared
    service.process(iid)
    original = view(app, prepared)['version_id']
    revise(app, prepared, clock)
    def generate(_settings, snapshot, previous):
        assert previous['overview']
        assert snapshot['events'][0]['status'] == 'outdated'
        assert 's20' in snapshot['sources']
        return report('s20')
    service.generator = generate
    service.process(iid)
    assert view(app, prepared)['known_through'] == 30
    with app.state.sessions() as session:
        session.get(PlaybackRun, rid).position_seconds = 10
        session.commit()
    early = view(app, prepared)
    assert early['version_id'] == original and early['state'] == 'historical' and not early['stale']
    assert 's20' not in early['report']['sources']
    with app.state.sessions() as session:
        session.get(PlaybackRun, rid).position_seconds = 5
        session.commit()
    assert view(app, prepared)['report'] is None


def test_failure_retains_previous_briefing_and_retry_is_durable(app, prepared, clock):
    service, iid, rid = prepared
    service.process(iid)
    original = view(app, prepared)['version_id']
    revise(app, prepared, clock)
    def fail(*_):
        raise RuntimeError('secret detail')
    service.generator = fail
    service.process(iid)
    data = view(app, prepared)
    assert data['state'] == 'retrying' and data['version_id'] == original and data['stale']
    assert 'secret' not in data['error']
    with app.state.sessions() as session:
        service.retry(session, session.get(PlaybackRun, rid))
    service.generator = lambda *_: report('s20')
    service.process(iid)
    assert view(app, prepared)['state'] == 'completed'


def test_worker_will_not_send_evidence_beyond_current_clock(app, prepared):
    service, iid, rid = prepared
    with app.state.sessions() as session:
        session.get(PlaybackRun, rid).position_seconds = 0
        session.commit()
    service.generator = lambda *_: pytest.fail('Future evidence sent to provider')
    service.process(iid)
    assert view(app, prepared)['report'] is None


def test_restart_during_generation_cannot_publish_old_run(app, prepared, clock):
    service, iid, rid = prepared
    def generate(*_):
        with app.state.sessions() as session:
            session.add(PlaybackRun(id='new-run', incident_id=iid, created_at=clock(), anchor_at=clock(),
                                    state='paused', position_seconds=0, revision=0))
            session.flush()
            session.get(Incident, iid).active_run_id = 'new-run'
            session.commit()
        return report()
    service.generator = generate
    service.process(iid)
    with app.state.sessions() as session:
        rows = list(session.query(SituationReportVersion).filter_by(run_id=rid))
        assert rows[0].status == 'cancelled'
        data = service.view(session, session.get(Incident, iid), session.get(PlaybackRun, 'new-run'))
        assert data['report'] is None


@pytest.mark.parametrize('invalid', [
    {'overview': []},
    {**report(), 'overview': [{'text': 'An unsupported claim', 'source_ids': ['invented']}]},
    {**report(), 'clarifications': [{'text': 'A question without evidence', 'source_ids': []}]},
])
def test_rejects_unsupported_report_items(invalid):
    with pytest.raises(ValueError):
        validate_report(invalid, {'s0': {}})


def test_api_returns_honest_empty_state(client, incident):
    response = client.get(f"/api/incidents/{incident['id']}/situation-report")
    assert response.status_code == 200
    assert response.json()['report'] is None
