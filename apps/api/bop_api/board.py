"""Deterministic source features and presentation timing; no model calls."""
import hashlib
import re


def source_registry(sources):
    registry = {}
    for source in sources:
        text = source.get('text') or ''
        registry[source['segment_id']] = {
            **source,
            'text_fingerprint': hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest(),
            # Literal mentions are navigation aids, not inferred event times or coordinates.
            'time_mentions': list(dict.fromkeys(re.findall(r'\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]m)?\b', text, re.I))),
            'extractor': 'stored_transcript',
        }
    return registry


def replay_manifest(nodes, edges):
    known = {n['id']: max(c['incident_end'] for c in n['citations']) for n in nodes}
    entries = []
    for node in nodes:
        entries.append((known[node['id']], 0, node))
    for edge in edges:
        if edge['source'] in known and edge['target'] in known:
            time = max(known[edge['source']], known[edge['target']], *(c['incident_end'] for c in edge['citations']))
            entries.append((time, 1, edge))
    cursor, steps = 1.0, []
    for time, rank, item in sorted(entries, key=lambda entry: (entry[0], entry[1], entry[2]['id'])):
        hold = min(12, max(4, 3 + len(item['label'].split()) / 3))
        steps.append({'id': item['id'], 'type': 'edge' if rank else 'node',
                      'at': round(cursor, 3), 'incident_time': time, 'hold': round(hold, 3)})
        cursor += hold
    return {'schema': 1, 'duration': round(cursor if steps else 0, 3), 'steps': steps}
