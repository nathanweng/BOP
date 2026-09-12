"""Completed-transcript knowledge graphs, with durable builds and source provenance."""
from datetime import timedelta
import hashlib
import json
import logging
from threading import Event, Thread
from urllib.request import Request
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update, or_
from sqlalchemy.exc import IntegrityError
from .board import replay_manifest, source_registry

from .events import _provider_response, _message_text, EventProviderError
from .models import (Incident, PlaybackRun, Recording, TranscriptSegment, KnowledgeVersion,
                     KnowledgeObservation, KnowledgeItem, KnowledgeSupport)
from .playback import playback_view
from .transcription import eligible_windows

logger = logging.getLogger(__name__)
KNOWLEDGE_PROMPT_VERSION = 3
NODE_KINDS = {'person', 'responder', 'location', 'object', 'event', 'claim', 'question', 'recording'}
EDGE_KINDS = {'stated_by', 'captured_by', 'supports', 'mentions', 'occurred_at', 'before', 'after',
              'updates', 'involves', 'located_at', 'moved_to', 'associated_with', 'corroborates',
              'duplicates_capture_of', 'disputes', 'contradicts', 'answers', 'raises_question', 'possible_same_as'}


def object_schema(properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


STRING = {'type': 'string'}
REFS = {'type': 'array', 'items': STRING}
ITEM = {'id': STRING, 'kind': STRING, 'label': STRING, 'description': STRING,
        'uncertainty': STRING, 'observation_ids': REFS}
SCHEMA = object_schema({
    'observations': {'type': 'array', 'items': object_schema({
        'id': STRING, 'segment_id': STRING, 'text': STRING, 'attribution': STRING})},
    'nodes': {'type': 'array', 'items': object_schema({**ITEM, 'kind': {'type': 'string', 'enum': sorted(NODE_KINDS)}})},
    'edges': {'type': 'array', 'items': object_schema({**ITEM, 'kind': {'type': 'string', 'enum': sorted(EDGE_KINDS)},
                                                    'source': STRING, 'target': STRING})},
})


def validate_chunk(data, sources, known_nodes):
    """Reject unsupported graph claims atomically; no invented citation IDs."""
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ('observations', 'nodes', 'edges')):
        raise ValueError('Invalid graph response')
    observations = {}
    for obs in data['observations']:
        if (not isinstance(obs, dict) or not all(isinstance(obs.get(k), str) and obs[k].strip()
                for k in ('id', 'segment_id', 'text', 'attribution')) or obs['segment_id'] not in sources
                or obs['id'] in observations or len(obs['attribution']) > 200):
            raise ValueError('Invalid observation citation')
        observations[obs['id']] = obs
    nodes = set(known_nodes)
    keys = set()
    for item in data['nodes'] + data['edges']:
        edge = 'source' in item
        if (not all(isinstance(item.get(k), str) for k in ('id', 'kind', 'label', 'description', 'uncertainty'))
                or not item['label'].strip() or not item['id'] or len(item['id']) > 90
                or len(item['label']) > 200 or len(item['uncertainty']) > 400
                or item['id'] in keys or item['id'] in known_nodes
                or item['kind'] not in (EDGE_KINDS if edge else NODE_KINDS)
                or not isinstance(item.get('observation_ids'), list) or not item['observation_ids']
                or any(ref not in observations for ref in item['observation_ids'])):
            raise ValueError('Invalid graph item or provenance')
        keys.add(item['id'])
        if not edge:
            nodes.add(item['id'])
    for edge in data['edges']:
        if edge.get('source') not in nodes or edge.get('target') not in nodes or edge['source'] == edge['target']:
            raise ValueError('Invalid relationship endpoints')
    return data


def source_excerpt(source):
    """Compact source fields for hashing and provider calls; word timings stay out of the prompt."""
    return {'segment_id': source['segment_id'], 'recording_id': source.get('recording_id') or '',
            'camera_label': source.get('camera_label') or '', 'incident_start': source.get('incident_start'),
            'incident_end': source.get('incident_end'), 'text': source.get('text') or ''}


def accepted_graph(data, sources, known_nodes, prefix='map_'):
    """Keep source-backed claims and drop invented citations instead of failing the whole board."""
    if not isinstance(data, dict):
        return {'observations': [], 'nodes': [], 'edges': []}
    observations = {}
    for obs in data.get('observations') or []:
        if (isinstance(obs, dict) and all(isinstance(obs.get(k), str) and obs[k].strip()
                for k in ('id', 'segment_id', 'text', 'attribution')) and obs['segment_id'] in sources
                and obs['id'].startswith(prefix) and obs['id'] not in observations
                and obs['id'] not in known_nodes and len(obs['attribution']) <= 200):
            observations[obs['id']] = obs
    nodes, keys, node_ids = [], set(), set(known_nodes)
    for item in data.get('nodes') or []:
        if _accepted_item(item, observations, keys, known_nodes, prefix, False):
            keys.add(item['id']); node_ids.add(item['id']); nodes.append(item)
    edges = []
    for item in data.get('edges') or []:
        if (_accepted_item(item, observations, keys, known_nodes, prefix, True)
                and item.get('source') in node_ids and item.get('target') in node_ids
                and item['source'] != item['target']):
            keys.add(item['id']); edges.append(item)
    cited = {ref for item in nodes + edges for ref in item['observation_ids']}
    return {'observations': [obs for obs in observations.values() if obs['id'] in cited],
            'nodes': nodes, 'edges': edges}


def _accepted_item(item, observations, keys, known_nodes, prefix, edge):
    return (isinstance(item, dict)
            and all(isinstance(item.get(k), str) for k in ('id', 'kind', 'label', 'description', 'uncertainty'))
            and item['label'].strip() and item['id'].startswith(prefix) and len(item['id']) <= 90
            and len(item['label']) <= 200 and len(item['uncertainty']) <= 400
            and item['id'] not in keys and item['id'] not in known_nodes
            and item['kind'] in (EDGE_KINDS if edge else NODE_KINDS)
            and isinstance(item.get('observation_ids'), list) and item['observation_ids']
            and all(ref in observations for ref in item['observation_ids'])
            and (not edge or (isinstance(item.get('source'), str) and isinstance(item.get('target'), str))))


def compact_graph(data, max_nodes=12, max_edges=18):
    """Keep the provider's importance order and remove evidence no retained item uses."""
    nodes = data['nodes'][:max_nodes]
    node_ids = {node['id'] for node in nodes}
    edges = [edge for edge in data['edges']
             if edge['source'] in node_ids and edge['target'] in node_ids][:max_edges]
    cited = {ref for item in nodes + edges for ref in item['observation_ids']}
    return {'observations': [obs for obs in data['observations'] if obs['id'] in cited],
            'nodes': nodes, 'edges': edges}


def generate_knowledge(settings, sources, progress=None):
    graph = {'observations': [], 'nodes': [], 'edges': []}
    # The model has enough context for the complete incident. One request lets it
    # merge repetitions globally and avoids producing one cluster per transcript batch.
    for start, batch in [(0, sources)]:
        prefix = 'map_'
        request = Request('https://openrouter.ai/api/v1/chat/completions', headers={
            'Authorization': f'Bearer {settings.openrouter_api_key}', 'Content-Type': 'application/json',
        }, data=json.dumps({
            'model': settings.openrouter_knowledge_model, 'temperature': 0.1, 'max_tokens': 8000,
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'knowledge_graph', 'strict': True, 'schema': SCHEMA}},
            'provider': {'require_parameters': True},
            'messages': [{'role': 'system', 'content': (
                'Create a concise command-level scene board from the complete incident transcript. '
                'This is a high-level synthesis, not a transcript outline or event-by-event timeline. '
                'Return at most 12 nodes and 18 edges total, ordered by operational importance. Prefer 6-10 nodes. '
                'Combine repeated statements and closely related details into one well-labeled node supported by '
                'multiple observations. Include only the central incident, major people or responder groups, pivotal '
                'locations or objects, material claims or contradictions, and questions personnel truly need clarified. '
                'Omit greetings, routine radio chatter, narration, minor actions, repeated details, and anything that '
                'would only restate a timestamp. Do not create recording nodes unless the recording itself is material. '
                'Use at most 30 short observations and the fewest needed to support each conclusion; an observation '
                'is evidence, not a node. Keep descriptions to one concise sentence. '
                'Represent reported statements as reports, never established truth. Every node and edge must '
                'cite observation_ids from this batch, each observation cites an exact segment_id. '
                'Never merge anonymous people across cameras or treat a camera label as a speaker. '
                'Unknown people must be recording-scoped and identities qualified. No invented names, police roles, '
                'guilt, intent, or tactical recommendations. Explicitly distinguish uncertainty from contradiction. '
                'Use possible_same_as for uncertain identity, uncertainty text for tentative relationships. '
                'For changes use later claim -> earlier claim edges: updates, disputes, or contradicts; '
                'Use contradicts for mutually exclusive accounts of the same detail, including explicit corrections '
                '(for example, the same reported object was a phone, not a knife). Use updates for compatible '
                'new detail or changed circumstances, and disputes for unresolved conflicting testimony. '
                'do not erase the earlier claim or call anyone a liar. Questions must cite what raises them. '
                f'Node kinds: {sorted(NODE_KINDS)}. Edge kinds: {sorted(EDGE_KINDS)}. '
                f'All IDs must start with {prefix}. Labels <=200 chars, uncertainty <=400 chars. '
                'Return observations, nodes, edges; empty arrays are allowed for no substantive evidence.'
            )}, {'role': 'user', 'content': json.dumps({
                'allowed_segment_ids': [s['segment_id'] for s in batch],
                'sources': [source_excerpt(s) for s in batch]})}],
        }).encode())
        try:
            result = _provider_response(request)
            raw = json.loads(_message_text(result['choices'][0]['message']))
        except EventProviderError:
            raise
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            logger.warning('Knowledge map parse failed: %s', exc)
            raise EventProviderError('The model returned an unreadable map. Retry the build.') from exc
        offered = sum(len(raw.get(k) or []) for k in ('nodes', 'edges')) if isinstance(raw, dict) else 0
        chunk = compact_graph(accepted_graph(raw, {s['segment_id'] for s in batch},
                                             {n['id'] for n in graph['nodes']}, prefix))
        if offered and not chunk['nodes'] and not chunk['edges']:
            raise EventProviderError('The model cited sources that are not in this incident. Retry the build.')
        existing_ids = {item['id'] for values in graph.values() for item in values}
        new_ids = [item['id'] for values in chunk.values() for item in values]
        if any(not key.startswith(prefix) or key in existing_ids for key in new_ids) or len(new_ids) != len(set(new_ids)):
            raise EventProviderError('The model returned duplicate map IDs. Retry the build.')
        for key in graph:
            graph[key].extend(chunk[key])
        if progress:
            progress(1, 1)
    return graph


class KnowledgeService:
    def __init__(self, settings, sessions, clock, generator=generate_knowledge):
        self.settings, self.sessions, self.clock, self.generator = settings, sessions, clock, generator
        self.halt = Event()
        self.thread = None

    def start(self):
        if self.settings.event_worker_interval_seconds > 0:
            self.thread = Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _loop(self):
        while not self.halt.wait(2):
            try:
                self.queue_ready()
                with self.sessions() as session:
                    ids = list(session.scalars(select(KnowledgeVersion.id).where(or_(
                        KnowledgeVersion.status == 'queued',
                        (KnowledgeVersion.status == 'processing') & (KnowledgeVersion.lease_until < self.clock())))))
                for version_id in ids:
                    if self.halt.is_set():
                        break
                    self.begin(version_id)
            except Exception:
                logger.exception('Knowledge map worker failed')

    def queue_ready(self):
        if not self.settings.openrouter_api_key:
            return
        with self.sessions() as session:
            ids = list(session.scalars(select(Incident.id)))
        for iid in ids:
            with self.sessions() as session:
                incident = session.scalar(select(Incident).where(Incident.id == iid).with_for_update())
                if not incident or not incident.active_run_id:
                    continue
                run = session.get(PlaybackRun, incident.active_run_id)
                readiness, _, _ = self.inputs(session, incident, run)
                if readiness['ready']:
                    try:
                        self.enqueue(session, incident, run, retry=False)
                    except IntegrityError:
                        session.rollback()  # Another API worker queued this input version.

    def inputs(self, session, incident, run):
        recordings = list(session.scalars(select(Recording).where(Recording.incident_id == incident.id).order_by(Recording.id)))
        segments = list(session.scalars(select(TranscriptSegment).where(TranscriptSegment.run_id == run.id)
                                        .order_by(TranscriptSegment.incident_start_seconds, TranscriptSegment.id)))
        lookup = {(s.recording_id, round(s.local_start_seconds, 6), round(s.local_end_seconds, 6)): s for s in segments}
        complete, expected, failed = 0, 0, 0
        selected = []
        for recording in recordings:
            for window in eligible_windows(incident_position_seconds=recording.start_offset_seconds + recording.duration_seconds,
                    start_offset_seconds=recording.start_offset_seconds, duration_seconds=recording.duration_seconds,
                    segment_seconds=self.settings.transcription_segment_seconds):
                expected += 1
                segment = lookup.get((recording.id, window.local_start, window.local_end))
                if segment and segment.status in ('completed', 'empty'):
                    complete += 1
                    selected.append(segment)
                elif segment and segment.status == 'failed':
                    failed += 1
        labels = {r.id: r.camera_label for r in recordings}
        sources = [{'segment_id': s.id, 'recording_id': s.recording_id, 'camera_label': labels[s.recording_id],
                    'incident_start': s.incident_start_seconds, 'incident_end': s.incident_end_seconds,
                    'local_start': s.local_start_seconds, 'local_end': s.local_end_seconds,
                    'speaker_words': json.loads(s.words_json) if s.words_json else [],
                    'text': s.text} for s in sorted(selected, key=lambda s: (s.incident_start_seconds, s.id))
                   if s.status == 'completed' and s.text and s.text.strip()]
        digest = hashlib.sha256(json.dumps({'schema': KNOWLEDGE_PROMPT_VERSION,
            'model': self.settings.openrouter_knowledge_model,
            'sources': [source_excerpt(s) for s in sources],
            'recordings': [(r.id, r.camera_label, r.start_offset_seconds, r.duration_seconds) for r in recordings],
            'windows': [(s.id, s.status) for s in selected]}, sort_keys=True).encode()).hexdigest()
        ended = playback_view(run, recordings, self.clock()).state == 'ended'
        # Complete terminal windows prove the run reached the end; rewinding while
        # the last transcription finishes must not block the automatic build.
        ready = bool(expected and complete == expected and self.settings.openrouter_api_key)
        reason = ('Add recordings to begin.' if not expected else 'Wait until playback ends.' if not ended else
                  f'Waiting for transcripts: {complete}/{expected} complete; {failed} failed.' if complete != expected else
                  'Set OPENROUTER_API_KEY in .env and restart the API.' if not self.settings.openrouter_api_key else '')
        return {'ready': ready, 'reason': '' if ready else reason, 'completed': complete, 'expected': expected, 'failed': failed}, sources, digest

    def enqueue(self, session, incident, run, retry=True, force=False):
        readiness, _, digest = self.inputs(session, incident, run)
        if not readiness['ready']:
            if not force:
                raise HTTPException(409, readiness['reason'])
            if not self.settings.openrouter_api_key:
                raise HTTPException(409, 'Set OPENROUTER_API_KEY in .env and restart the API.')
            if not readiness['expected']:
                raise HTTPException(409, readiness['reason'] or 'Add recordings to begin.')
            if not readiness['completed']:
                raise HTTPException(409, 'No transcript windows are complete yet.')
        version = session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.run_id == run.id, KnowledgeVersion.input_hash == digest))
        if version is None:
            version = KnowledgeVersion(id=str(uuid4()), run_id=run.id, input_hash=digest, status='queued',
                                       model=self.settings.openrouter_knowledge_model, created_at=self.clock())
            session.add(version)
        elif version.status == 'failed' and retry:
            version.status, version.error = 'queued', None
        session.commit()
        return {'id': version.id, 'status': version.status}

    def begin(self, version_id):
        Thread(target=self.process, args=(version_id,), daemon=True).start()

    def process(self, version_id):
        token = str(uuid4())
        with self.sessions() as session:
            claimed = session.execute(update(KnowledgeVersion).where(KnowledgeVersion.id == version_id, or_(
                KnowledgeVersion.status == 'queued', (KnowledgeVersion.status == 'processing') &
                (KnowledgeVersion.lease_until < self.clock()))).values(status='processing', lease_token=token,
                    stage='analyzing', completed_batches=0,
                    lease_until=self.clock() + timedelta(minutes=5))).rowcount
            session.commit()
            if not claimed:
                return
            version = session.get(KnowledgeVersion, version_id)
            run = session.get(PlaybackRun, version.run_id)
            incident = session.get(Incident, run.incident_id)
            _, sources, digest = self.inputs(session, incident, run)
            version.total_batches = 1
            session.commit()
        try:
            def progress(completed, total):
                with self.sessions() as progress_session:
                    progress_session.execute(update(KnowledgeVersion).where(KnowledgeVersion.id == version_id,
                        KnowledgeVersion.lease_token == token).values(completed_batches=completed, total_batches=total,
                        stage='preparing' if completed == total else 'analyzing',
                        lease_until=self.clock() + timedelta(minutes=5)))
                    progress_session.commit()
            graph = (self.generator(self.settings, sources, progress) if self.generator is generate_knowledge
                     else self.generator(self.settings, sources))
            with self.sessions() as session:
                # Same incident lock as playback controls: restart cannot race graph publication.
                incident = session.scalar(select(Incident).where(Incident.id == incident.id).with_for_update())
                run = session.get(PlaybackRun, run.id)
                version = session.get(KnowledgeVersion, version_id)
                _, _, fresh_digest = self.inputs(session, incident, run)
                if version.lease_token != token:
                    return
                if incident.active_run_id != run.id or fresh_digest != digest or digest != version.input_hash:
                    raise ValueError('Graph inputs changed during build; retry on the current run.')
                observation_ids = {}
                for obs in graph['observations']:
                    oid = str(uuid4())
                    observation_ids[obs['id']] = oid
                    session.add(KnowledgeObservation(id=oid, version_id=version_id, segment_id=obs['segment_id'],
                                                     text=obs['text'], attribution=obs['attribution']))
                session.flush()
                for item in graph['nodes'] + graph['edges']:
                    iid = str(uuid4())
                    session.add(KnowledgeItem(id=iid, version_id=version_id, item_key=item['id'], kind=item['kind'],
                        label=item['label'], description=item['description'], uncertainty=item['uncertainty'],
                        source_key=item.get('source'), target_key=item.get('target')))
                    session.flush()
                    for ref in set(item['observation_ids']):
                        session.add(KnowledgeSupport(item_id=iid, observation_id=observation_ids[ref]))
                version.status, version.error, version.lease_until = 'completed', None, None
                version.stage = 'ready'
                version.completed_batches = version.total_batches
                version.sources_json = json.dumps(source_registry(sources))
                session.flush()
                projection = self.view(session, incident, run)
                version.replay_json = json.dumps(replay_manifest(projection['nodes'], projection['edges']))
                session.commit()
        except EventProviderError as exc:
            logger.warning('Knowledge map build failed: %s', exc)
            error = str(exc)[:500]
        except Exception:
            logger.exception('Knowledge map build failed')
            error = 'Map build failed validation or its inputs changed. Saved maps are retained. Retry the build.'
        else:
            return
        with self.sessions() as session:
            session.execute(update(KnowledgeVersion).where(KnowledgeVersion.id == version_id,
                KnowledgeVersion.lease_token == token).values(status='failed', stage='failed', lease_until=None,
                error=error))
            session.commit()

    def view(self, session, incident, run, cutoff=None):
        readiness, _, digest = self.inputs(session, incident, run)
        versions = list(session.scalars(select(KnowledgeVersion).where(KnowledgeVersion.run_id == run.id)
                                       .order_by(KnowledgeVersion.created_at.desc())))
        latest = versions[0] if versions else None
        valid = next((v for v in versions if v.status == 'completed'), None)
        result = {'run_id': run.id, **readiness, 'state': latest.status if latest else 'not_built',
                  'error': latest.error if latest else None, 'version_id': valid.id if valid else None,
                  'stale': bool(valid and valid.input_hash != digest), 'nodes': [], 'edges': []}
        result['progress'] = {'stage': latest.stage if latest else 'waiting',
                              'completed_batches': latest.completed_batches if latest else 0,
                              'total_batches': latest.total_batches if latest else 0}
        if not valid:
            return result
        observations = {o.id: o for o in session.scalars(select(KnowledgeObservation).where(KnowledgeObservation.version_id == valid.id))}
        segments = {s.id: s for s in session.scalars(select(TranscriptSegment).where(TranscriptSegment.run_id == run.id))}
        items = list(session.scalars(select(KnowledgeItem).where(KnowledgeItem.version_id == valid.id)))
        supports = {}
        for support in session.scalars(select(KnowledgeSupport).join(KnowledgeItem).where(KnowledgeItem.version_id == valid.id)):
            supports.setdefault(support.item_id, []).append(observations[support.observation_id])
        for item in items:
            evidence = supports.get(item.id, [])
            # Conservative cutoff: a label is visible only after ALL its source material is available.
            if not evidence or any(cutoff is not None and segments[o.segment_id].incident_end_seconds > cutoff for o in evidence):
                continue
            citations = []
            for obs in evidence:
                segment = segments[obs.segment_id]
                citations.append({'id': obs.id, 'segment_id': segment.id, 'recording_id': segment.recording_id,
                    'observation': obs.text, 'attribution': obs.attribution, 'text': segment.text,
                    'local_start': segment.local_start_seconds, 'local_end': segment.local_end_seconds,
                    'incident_start': segment.incident_start_seconds, 'incident_end': segment.incident_end_seconds})
            result['edges' if item.source_key else 'nodes'].append({'id': item.item_key, 'kind': item.kind,
                'label': item.label, 'description': item.description, 'uncertainty': item.uncertainty,
                'source': item.source_key, 'target': item.target_key, 'status': 'active', 'citations': citations})
        node_ids = {n['id'] for n in result['nodes']}
        result['edges'] = [e for e in result['edges'] if e['source'] in node_ids and e['target'] in node_ids]
        for node in result['nodes']:
            kinds = {e['kind'] for e in result['edges'] if e['target'] == node['id']}
            node['status'] = 'contradicted' if 'contradicts' in kinds else 'disputed' if 'disputes' in kinds else 'superseded' if 'updates' in kinds else 'active'
        result['replay'] = (json.loads(valid.replay_json) if valid.replay_json and cutoff is None
                            else replay_manifest(result['nodes'], result['edges']))
        # Legacy saved graphs receive the deterministic schedule without a provider rebuild.
        result['sources'] = json.loads(valid.sources_json) if valid.sources_json else {}
        if cutoff is not None:
            result['sources'] = {key: s for key, s in result['sources'].items() if s['incident_end'] <= cutoff}
        return result
