import type { Recording, Transcripts } from './types';

export interface CallSitrep {
  location: string;
  caller: string;
  situation: string;
  details: string[];
}

function normalize(text: string | null | undefined): string {
  return (text ?? '').replace(/\s+/g, ' ').trim();
}

function firstText(transcripts: Transcripts | undefined): string {
  if (!transcripts) return '';
  const parts: string[] = [];
  for (const recording of transcripts.recordings ?? []) {
    for (const segment of recording.segments ?? []) {
      if (segment.text) parts.push(segment.text);
      for (const turn of segment.turns ?? []) {
        if (turn.text) parts.push(turn.text);
      }
    }
  }
  return parts.join(' ');
}

function extractCaller(text: string): string {
  const match = text.match(/(?:this is|caller is|speaking is|i am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)/i)
    ?? text.match(/([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(?:calling|here|speaking)/i)
    ?? text.match(/(?:my name is|i am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)/i);
  return match ? normalize(match[1]) : 'Caller identity not clear';
}

function extractLocation(text: string): string {
  const match = text.match(/(?:at|outside|near|inside|in)\s+[^.!?]{0,120}(?:street|st|ave|avenue|road|rd|boulevard|blvd|drive|dr|lane|ln|way|apartment|apt|complex|building|house|home)[^.!?]{0,80}/i)
    ?? text.match(/(?:\d+\s+[A-Za-z0-9. ]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Ct|Circle|Cir|Parkway|Pkwy))/i)
    ?? text.match(/(?:\d+\s+[A-Za-z0-9. ]{3,60})/i);
  return match ? normalize(match[0].replace(/^(?:at|outside|near|inside|in)\s+/i, '')) : 'Location not established';
}

function extractSituation(text: string): string {
  if (/gun|armed|weapon|shooting|shots fired|firearm/i.test(text)) {
    return 'Armed subject / weapon report at the location.';
  }
  if (/medical|injured|bleeding|unconscious|patient|ems|ambulance/i.test(text)) {
    return 'Medical emergency reported.';
  }
  if (/fire|smoke|explosion|hazmat|chemical|gas/i.test(text)) {
    return 'Fire / hazardous material situation reported.';
  }
  if (/accident|crash|traffic|car wreck|vehicle collision/i.test(text)) {
    return 'Traffic collision or vehicle incident reported.';
  }
  return 'Active incident reported requiring response.';
}

export function buildCallSitrep(
  recordings: Recording[] | undefined,
  transcripts: Transcripts | undefined,
): CallSitrep | null {
  const hasAudio = (recordings ?? []).some((recording) => /\.(wav|mp3|m4a|aac|flac|ogg)$/i.test(recording.original_filename));
  const transcriptText = firstText(transcripts);
  if (!hasAudio || !transcriptText) return null;

  const text = normalize(transcriptText);
  const location = extractLocation(text);
  const caller = extractCaller(text);
  const situation = extractSituation(text);

  const details = [
    `Caller: ${caller}`,
    `Location: ${location}`,
    `Situation: ${situation}`,
    /police|officer|dispatch|unit|responders/i.test(text) ? 'Police response requested or en route.' : 'Dispatch status unclear from the call.',
    /ems|medical|ambulance|injured|patient/i.test(text) ? 'EMS/medical support appears needed.' : 'No clear EMS detail was captured in the transcript.',
  ];

  return { location, caller, situation, details };
}
