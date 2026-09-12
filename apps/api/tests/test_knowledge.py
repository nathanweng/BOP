from dataclasses import replace
from copy import deepcopy

import pytest

from fastapi import HTTPException

from bop_api.knowledge import KnowledgeService, compact_graph, validate_chunk, generate_knowledge, accepted_graph
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


def enqueue(app, prepared, force=False):
    service, iid, rid = prepared
    with app.state.sessions() as session:
        return service.enqueue(session, session.get(Incident, iid), session.get(PlaybackRun, rid), force=force)['id']


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


def test_playback_revision_change_does_not_cancel_valid_build(app, prepared):
    service = prepared[0]
    def generate(*_):
        with app.state.sessions() as session:
            session.get(PlaybackRun, prepared[2]).revision += 1
            session.commit()
        return fixture_graph()
    service.generator = generate
    service.process(enqueue(app, prepared))
    result = view(app, prepared)
    assert result['state'] == 'completed' and len(result['nodes']) == 2


def test_auto_queue_once_even_after_rewind_and_do_not_retry_failures_forever(app, prepared):
    service = prepared[0]
    with app.state.sessions() as session:
        session.get(PlaybackRun, prepared[2]).position_seconds = 0
        session.commit()
    service.queue_ready()
    service.queue_ready()
    with app.state.sessions() as session:
        rows = list(session.query(KnowledgeVersion).filter_by(run_id=prepared[2]))
        assert len(rows) == 1
        rows[0].status = 'failed'
        session.commit()
    service.queue_ready()
    assert view(app, prepared)['state'] == 'failed'


def test_saved_replay_is_ordered_deterministic_and_preserves_sources(app, prepared):
    service = prepared[0]
    service.process(enqueue(app, prepared))
    result = view(app, prepared)
    steps = result['replay']['steps']
    assert [s['id'] for s in steps] == ['n1', 'n2', 'e1']
    assert steps[-1]['incident_time'] == 22
    assert steps[-1]['at'] > steps[1]['at']
    assert result['sources']['s0']['extractor'] == 'stored_transcript'
    assert result['progress']['stage'] == 'ready'
    assert view(app, prepared)['replay'] == result['replay']


def test_restart_during_build_prevents_publication(app, prepared):
    service = prepared[0]
    def generate(*_):
        with app.state.sessions() as session:
            session.get(Incident, prepared[1]).active_run_id = None
            session.commit()
        return fixture_graph()
    service.generator = generate
    service.process(enqueue(app, prepared))
    with app.state.sessions() as session:
        row = session.query(KnowledgeVersion).filter_by(run_id=prepared[2]).one()
        assert row.status == 'failed' and row.replay_json is None


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


def test_force_enqueue_builds_from_completed_windows_before_the_run_is_ready(app, prepared):
    with app.state.sessions() as session:
        session.get(TranscriptSegment, 's20').status = 'failed'
        session.commit()
    assert not view(app, prepared)['ready']
    with pytest.raises(HTTPException) as blocked:
        enqueue(app, prepared)
    assert blocked.value.status_code == 409
    assert enqueue(app, prepared, force=True)
    with app.state.sessions() as session:
        for sid in ('s0', 's10', 's20'):
            session.get(TranscriptSegment, sid).status = 'failed'
        session.commit()
    with pytest.raises(HTTPException) as empty:
        enqueue(app, prepared, force=True)
    assert empty.value.status_code == 409


def test_api_readiness_and_build_gate(client, incident):
    path = f"/api/incidents/{incident['id']}/knowledge"
    assert client.get(path).json()['ready'] is False
    assert client.post(path).status_code == 409
    assert client.post(path + '?force=true').status_code == 409
    assert client.get(path + '?cutoff_seconds=-1').status_code == 422


def test_accepted_graph_keeps_supported_claims_and_drops_invented_citations():
    graph = deepcopy(fixture_graph())
    graph['observations'][0]['id'] = 'map_o1'
    graph['observations'][1]['id'] = 'map_o2'
    graph['nodes'][0]['id'] = 'map_n1'
    graph['nodes'][0]['observation_ids'] = ['map_o1']
    graph['nodes'][1]['id'] = 'map_n2'
    graph['nodes'][1]['observation_ids'] = ['map_o2']
    graph['edges'][0]['id'] = 'map_e1'
    graph['edges'][0]['source'] = 'map_n2'
    graph['edges'][0]['target'] = 'map_n1'
    graph['edges'][0]['observation_ids'] = ['map_o2']
    graph['observations'].append(dict(id='map_bad', segment_id='invented', text='no', attribution='Witness'))
    graph['nodes'].append(dict(id='map_dropped', kind='claim', label='Invented', description='', uncertainty='',
                               observation_ids=['map_bad']))
    result = accepted_graph(graph, {'s0', 's10'}, set())
    assert [n['id'] for n in result['nodes']] == ['map_n1', 'map_n2']
    assert [e['id'] for e in result['edges']] == ['map_e1']
    assert {obs['id'] for obs in result['observations']} == {'map_o1', 'map_o2'}


def test_generator_sends_complete_history_once_with_fast_high_level_settings(monkeypatch, settings):
    import json
    requests = []
    def provider(request):
        body = json.loads(request.data)
        content = json.loads(body['messages'][1]['content'])
        requests.append((body, content))
        return {'choices': [{'message': {'content': json.dumps({'observations': [], 'nodes': [], 'edges': []})}}]}
    monkeypatch.setattr('bop_api.knowledge._provider_response', provider)
    sources = [{'segment_id': f's{i}', 'text': 'Silence marker.'} for i in range(25)]
    generate_knowledge(settings, sources)
    assert [s['segment_id'] for s in requests[0][1]['sources']] == [s['segment_id'] for s in sources]
    assert requests[0][1]['allowed_segment_ids'] == [s['segment_id'] for s in sources]
    assert all('speaker_words' not in s for s in requests[0][1]['sources'])
    assert requests[0][0]['model'] == settings.openrouter_knowledge_model
    assert requests[0][0]['max_tokens'] == 8000
    assert 'at most 12 nodes and 18 edges' in requests[0][0]['messages'][0]['content']


def test_compact_graph_enforces_scene_board_limits_and_removes_unused_evidence():
    observations = [dict(id=f'o{i}', segment_id='s0', text='source', attribution='Witness')
                    for i in range(20)]
    nodes = [dict(id=f'n{i}', kind='claim', label=f'Claim {i}', description='', uncertainty='',
                  observation_ids=[f'o{i}']) for i in range(15)]
    edges = [dict(id=f'e{i}', kind='supports', label='Supports', description='', uncertainty='',
                  observation_ids=['o0'], source='n0', target=f'n{i + 1}') for i in range(14)]
    result = compact_graph({'observations': observations, 'nodes': nodes, 'edges': edges})
    assert len(result['nodes']) == 12
    assert len(result['edges']) == 11
    assert {obs['id'] for obs in result['observations']} == {f'o{i}' for i in range(12)}


def test_thumbnail_is_scoped_to_saved_run_and_exact_source_window(client, app, prepared, monkeypatch):
    from types import SimpleNamespace
    service = prepared[0]
    vid = enqueue(app, prepared)
    service.process(vid)
    with app.state.sessions() as session:
        session.get(Recording, 'cam').storage_key = 'f' * 32 + '.mp4'
        session.commit()
    calls = []
    def extract(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b'jpeg', stderr=b'')
    monkeypatch.setattr('bop_api.main.subprocess.run', extract)
    path = f'/api/incidents/{prepared[1]}/knowledge/{vid}/sources/s0/thumbnail'
    response = client.get(path)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert calls[0][calls[0].index('-ss') + 1] == '5.0'
    assert client.get(path.replace('/s0/', '/unknown/')).status_code == 404
    with app.state.sessions() as session:
        session.get(Incident, prepared[1]).active_run_id = None
        session.commit()
    assert client.get(path).status_code == 404
