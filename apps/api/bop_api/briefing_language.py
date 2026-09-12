"""Shared, source-grounded wording for law-enforcement briefings."""
import re

BRIEFING_STYLE = (
    'Write for law-enforcement personnel in concise, natural language. '
    'Use Officer, Driver, Caller or Witness only when the cited dialogue supports that role '
    '(for example self-identification or unambiguous first-person duties/context). '
    'Do not assume a voice is an officer because this is body-camera footage or because it asks a question. '
    'Anonymous speaker numbers are local to each transcript window, not persistent identities. '
    'When the role is unclear, omit the generic subject: use "Possible injury reported", '
    '"Reports that the other driver is leaving" or "Asks whether anyone is injured". '
    'Do not write "Speaker reports", "A speaker" or "Someone asks". '
    'Preserve reported/alleged/possible qualifiers; a report is not a verified fact. '
    'Never infer identities across cameras; camera labels are not speaker identities. '
)


def concise_attribution(text: str) -> str:
    """Remove only a generic sentence-leading subject, retaining its reporting verb.

    This also improves saved output without rewriting evidence or guessing a role.
    Embedded references, pronouns and quotes are deliberately left untouched.
    """
    return re.sub(
        r'^(?:(?:a |the )?speaker(?: \d+)?|someone)\s+'
        r'(reports?|reported|asks?|asked|states?|stated|says?|said|requests?|requested)\b',
        lambda match: match[1].capitalize(), text, flags=re.IGNORECASE,
    )
