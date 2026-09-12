import pytest

from bop_api.briefing_language import concise_attribution
from bop_api.events import normalize_events
from bop_api.situation import clean_citation_suffix


@pytest.mark.parametrize('original, expected', [
    ('Speaker reports possibly needing medical care', 'Reports possibly needing medical care'),
    ('Someone asks who is injured at scene', 'Asks who is injured at scene'),
    ('A speaker reports that her daughter was in the car.', 'Reports that her daughter was in the car.'),
    ('Speaker 2 reported a possible injury.', 'Reported a possible injury.'),
    ('Officer reports possible injuries.', 'Officer reports possible injuries.'),
    ('The speaker in the vehicle is inaudible.', 'The speaker in the vehicle is inaudible.'),
    ('Witness says "Speaker reports" was transcribed.', 'Witness says "Speaker reports" was transcribed.'),
])
def test_remove_generic_subject_without_inventing_roles_or_changing_claims(original, expected):
    assert concise_attribution(original) == expected


def test_saved_output_keeps_identity_citations_and_original_evidence():
    original = {'segment_id': 's1', 'title': 'Speaker reports a possible injury',
                'timestamp_seconds': 42, 'source_text': 'Speaker 1: I might be hurt.'}
    normalized = normalize_events([original])[0]
    assert normalized['title'] == 'Reports a possible injury'
    assert normalized['source_text'] == original['source_text']
    assert normalized['timestamp_seconds'] == 42
    assert normalize_events([normalized])[0]['id'] == normalized['id']
    assert original['title'].startswith('Speaker')
    sentence = clean_citation_suffix({'text': 'A speaker reports a possible injury.',
                                     'event_ids': [normalized['id']], 'source_ids': ['s1']})
    assert sentence['text'] == 'Reports a possible injury.'
    assert sentence['source_ids'] == ['s1']
