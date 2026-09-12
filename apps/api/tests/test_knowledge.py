from dataclasses import replace
from copy import deepcopy

import pytest

from bop_api.knowledge import KnowledgeService, validate_chunk, generate_knowledge
from bop_api.models import Incident, PlaybackRun, Recording, TranscriptSegment, KnowledgeVersion


def fixture_graph():
    def node(key, label, ref):
        return dict(id=key, kind='claim', label=label, description=label, uncertainty='', observation_ids=[ref])
    return {'observations': [dict(id='o1', segment_id='s0', text='A witness reports a knife.', attribution='Witness'),
                             dict(id='o2', segment_id='s10', text='The witness corrects it to a phone.', attribution='Witness')],
            'nodes': [node('n1', 'Reported knife', 'o1'), node('n2', 'Correction: phone', 'o2')],
            'edges': [{**node('e1', 'Corrects earlier object report', 'o2'), 'kind': 'contradicts', 'source': 'n2', 'target': 'n1'}]}


@pytest.fixture
def prepared(app, incident, clock):
    run_id = incident['playback']['run_id']
    with app.state.sessions() as session:
        session.add(Recording(id='cam', incident_id=incident['id'], original_filename='test.mp4',
            camera_label='Camera A', storage_key='knowledge-test.mp4', duration_seconds=25,
            start_offset_seconds=2, size_bytes=100, created_at=clock()))
        session.get(PlaybackRun, run_id).position_seconds = 27
        session.commit()
        for start, end, status in [(0, 10, 'completed'), (10, 20, 'completed'), (20, 25, 'empty')]:
            session.add(TranscriptSegment(id=f's{start}', run_id=run_id, recording_id='cam',
                local_start_seconds=start, local_end_seconds=end, incident_start_seconds=start + 2,
                incident_end_seconds=end + 2, status=status, text='source' if status == 'completed' else None,
                created_at=clock()))
        session.commit()
    service = KnowledgeService(replace(app.state.settings, openrouter_api_key='test'), app.state.sessions, clock,
                               lambda *_: fixture_graph())
    return service, incident['id'], run_id


def view(app, prepared, cutoff=None):
    service, iid, rid = prepared
    with app.state.sessions() as session:
        return service.view(session, session.get(Incident, iid), session.get(PlaybackRun, rid), cutoff)


def enqueue(app, prepared):
    service, iid, rid = prepared
    with app.state.sessions() as session:
        return service.enqueue(session, session.get(Incident, iid), session.get(PlaybackRun, rid))['id']


def test_readiness_requires_every_window_including_silent_tail_and_playback_end(app, prepared):
    assert view(app, prepared)['ready']
    with app.state.sessions() as session:
        session.get(TranscriptSegment, 's20').status = 'failed'
        session.commit()
    result = view(app, prepared)
    assert not result['ready'] and result['failed'] == 1 and result['expected'] == 3
    with app.state.sessions() as session:
        session.delete(session.get(TranscriptSegment, 's20'))
        session.commit()
    assert not view(app, prepared)['ready']
    with app.state.sessions() as session:
        session.get(PlaybackRun, prepared[2]).position_seconds = 5
        session.commit()
    assert view(app, prepared)['reason'] == 'Wait until playback ends.'


def test_persistence_idempotence_and_no_future_status_or_citation_leak(app, prepared):
    service = prepared[0]
    vid = enqueue(app, prepared)
    assert enqueue(app, prepared) == vid
    service.process(vid)
    final = view(app, prepared)
    assert final['state'] == 'completed'
    assert final['nodes'][0]['status'] == 'contradicted'
    early = view(app, prepared, 12)
    assert [n['label'] for n in early['nodes']] == ['Reported knife']
    assert early['nodes'][0]['status'] == 'active'
    assert early['edges'] == []
    assert view(app, prepared, 11)['nodes'] == []
    assert enqueue(app, prepared) == vid
    service.generator = lambda *_: pytest.fail('Completed graph must not regenerate')
    service.process(vid)


def test_failed_rebuild_retains_prior_map_and_hides_internal_error(app, prepared, clock):
    service = prepared[0]
    service.process(enqueue(app, prepared))
    original = view(app, prepared)['version_id']
    clock.advance(1)
    with app.state.sessions() as session:
        session.get(TranscriptSegment, 's0').text = 'Revised transcript'
        session.commit()
    def fail(*_):
        raise RuntimeError('secret provider detail')
    service.generator = fail
    vid = enqueue(app, prepared)
    service.process(vid)
    result = view(app, prepared)
    assert result['state'] == 'failed' and result['version_id'] == original and result['stale']
    assert 'secret' not in result['error']
    assert enqueue(app, prepared) == vid


def test_revision_change_during_generation_cannot_publish(app, prepared):
    service = prepared[0]
    def generate(*_):
        with app.state.sessions() as session:
            session.get(PlaybackRun, prepared[2]).revision += 1
            session.commit()
        return fixture_graph()
    service.generator = generate
    service.process(enqueue(app, prepared))
    result = view(app, prepared)
    assert result['state'] == 'failed' and result['nodes'] == []


def test_expired_worker_lease_recovers(app, prepared, clock):
    service = prepared[0]
    vid = enqueue(app, prepared)
    with app.state.sessions() as session:
        row = session.get(KnowledgeVersion, vid)
        row.status, row.lease_token, row.lease_until = 'processing', 'old', clock()
        session.commit()
    clock.advance(1)
    service.process(vid)
    assert view(app, prepared)['state'] == 'completed'


@pytest.mark.parametrize('mutation', ['citation', 'endpoint', 'kind', 'empty_support'])
def test_invalid_provider_graph_rejected(mutation):
    graph = deepcopy(fixture_graph())
    if mutation == 'citation': graph['observations'][0]['segment_id'] = 'invented'
    if mutation == 'endpoint': graph['edges'][0]['target'] = 'invented'
    if mutation == 'kind': graph['nodes'][0]['kind'] = 'guilty_suspect'
    if mutation == 'empty_support': graph['nodes'][0]['observation_ids'] = []
    with pytest.raises(ValueError):
        validate_chunk(graph, {'s0', 's10'}, set())


def test_api_readiness_and_build_gate(client, incident):
    path = f"/api/incidents/{incident['id']}/knowledge"
    assert client.get(path).json()['ready'] is False
    assert client.post(path).status_code == 409
    assert client.get(path + '?cutoff_seconds=-1').status_code == 422


def test_generator_processes_every_batch_without_silent_truncation(monkeypatch, settings):
    import json
    requests = []
    def provider(request):
        body = json.loads(request.data)
        content = json.loads(body['messages'][1]['content'])
        requests.append(content['sources'])
        return {'choices': [{'message': {'content': json.dumps({'observations': [], 'nodes': [], 'edges': []})}}]}
    monkeypatch.setattr('bop_api.knowledge._provider_response', provider)
    sources = [{'segment_id': f's{i}', 'text': 'Silence marker.'} for i in range(25)]
    generate_knowledge(settings, sources)
    assert [len(batch) for batch in requests] == [12, 12, 1]
    assert [source for batch in requests for source in batch] == sources
