import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import type { Recording } from './types';

interface ReportItem { text: string; source_ids: string[] }
interface Source { id: string; recording_id: string; camera_label: string; local_start: number; local_end: number;
  incident_start: number; incident_end: number; text: string }
interface Report { sections: Record<'overview' | 'developments' | 'scene_status' | 'clarifications', ReportItem[]>;
  sources: Record<string, Source> }
export interface SituationData { run_id: string; state: string; error?: string; version_id?: string;
  known_through: number | null; awaiting_analysis: number; stale: boolean; report: Report | null }

export function SituationReport({ incidentId, runId, recordings, incidentTime }: {
  incidentId: string; runId: string; recordings: Recording[]; incidentTime: number;
}) {
  const query = useQuery({ queryKey: ['situation-report', incidentId, runId],
    queryFn: ({ signal }) => api.getSituationReport(incidentId, signal), refetchInterval: 3000, retry: false });
  const retry = useMutation({ mutationFn: () => api.retrySituationReport(incidentId),
    onSuccess: () => { void query.refetch(); } });
  const data = query.data?.run_id === runId ? query.data : undefined;
  // Hide the prior response immediately when the user scrubs back, before the next API poll.
  const report = data?.known_through != null && data.known_through <= incidentTime ? data.report : null;
  const updating = data?.state === 'queued' || data?.state === 'processing' || data?.state === 'retrying';
  return <section className="panel personnel-sitrep" aria-labelledby="sitrep-heading">
    <div className="section-heading"><div><p className="eyebrow">Personnel briefing</p>
      <h2 id="sitrep-heading">Situational report</h2></div>
      <span className="status">{updating ? 'Updating briefing' : report ? 'Statement-based' : 'Awaiting analysis'}</span></div>
    <p className="muted">What has happened at the scene, the latest reported situation, and what still needs clarification.</p>
    {report && <p className="sitrep-freshness">Based on analyzed sources through <strong>{formatTime(data!.known_through!)}</strong>.
      {data?.stale && ' New analyzed evidence is not included yet; this is the previous briefing.'}
      {!!data?.awaiting_analysis && ` ${data.awaiting_analysis} transcript segment(s) awaiting event analysis.`}</p>}
    {(query.isError || retry.isError || data?.error) && <div role="alert" className="error">
      <p>{data?.error || messageFor(query.error || retry.error)}</p>
      <button disabled={retry.isPending} onClick={() => query.isError ? void query.refetch() : retry.mutate()}>Retry briefing</button></div>}
    {!report && <p className="empty-state" data-testid="analysis-empty-state">
      {data?.state === 'disabled' ? 'Configure OpenRouter on the API to generate the personnel briefing.'
        : updating ? 'Preparing a briefing from analyzed statements. It will appear here automatically.'
          : 'No analyzed briefing is available at this point in the incident. It will update automatically as source-linked information becomes available.'}</p>}
    {report && <ReportContent key={data?.version_id} report={report} recordings={recordings} />}
  </section>;
}

function ReportContent({ report, recordings }: { report: Report; recordings: Recording[] }) {
  const [selected, setSelected] = useState<ReportItem>();
  const [sourceId, setSourceId] = useState<string>();
  const source = sourceId ? report.sources[sourceId] : undefined;
  const recording = recordings.find((r) => r.id === source?.recording_id);
  const sections = [
    ['overview', 'Scene overview', 'No supported scene overview yet.'],
    ['developments', 'Key developments', 'No additional developments identified in this update.'],
    ['scene_status', 'Latest reported status', 'Current scene conditions have not been established by the analyzed statements.'],
    ['clarifications', 'Clarification needed', 'No specific clarification questions identified in this update. This does not establish that all information is confirmed.'],
  ] as const;
  return <>
    <div className="sitrep-sections">{sections.map(([key, title, empty]) => <section key={key}
      className={`sitrep-section sitrep-${key}`} aria-label={title}><h3>{title}</h3>
      {report.sections[key].length ? <ul>{report.sections[key].map((item, index) => <li key={index}>
        <p>{item.text}</p><small className="muted">Latest source: {formatTime(Math.max(...item.source_ids.map((id) => report.sources[id].incident_start)))}</small>
        {' · '}<button className="sitrep-source-link" onClick={() => { setSelected(item); setSourceId(item.source_ids[0]); }}>
          Review supporting sources · {item.source_ids.length}</button>
      </li>)}</ul> : <p className="muted">{empty}</p>}
    </section>)}</div>
    {selected && <aside className="sitrep-evidence" aria-label="Briefing evidence">
      <div className="section-heading"><h3>Supporting evidence</h3><button onClick={() => { setSelected(undefined); setSourceId(undefined); }}>Close briefing evidence</button></div>
      <p>{selected.text}</p><div className="button-row">{selected.source_ids.map((id) => {
        const s = report.sources[id];
        return <button key={id} aria-pressed={id === sourceId} onClick={() => setSourceId(id)}>{s.camera_label} · {formatTime(s.incident_start)}</button>;
      })}</div>
      {source && <blockquote>{source.text}</blockquote>}
      {source && recording && <video key={source.id} controls preload="metadata" src={recording.media_url} aria-label="Briefing source video"
        onLoadedMetadata={(event) => { event.currentTarget.currentTime = source.local_start; }}
        onSeeking={(event) => { const v = event.currentTarget; if (v.currentTime < source.local_start || v.currentTime > source.local_end) v.currentTime = source.local_start; }}
        onTimeUpdate={(event) => { const v = event.currentTarget; if (v.currentTime >= source.local_end) { v.pause(); v.currentTime = source.local_start; } }} />}
    </aside>}
  </>;
}
