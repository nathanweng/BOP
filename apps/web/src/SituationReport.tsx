import { useMutation, useQuery } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import type { HistoryEvent } from './types';

interface ReportItem { text: string; source_ids: string[]; event_ids: string[] }
interface Report {
  sections: Record<'overview' | 'developments' | 'scene_status' | 'clarifications', ReportItem[]>;
  events: Record<string, HistoryEvent>;
}
export interface SituationData { run_id: string; state: string; error?: string; version_id?: string;
  known_through: number | null; awaiting_analysis: number; stale: boolean; report: Report | null }

const labels = { current: 'Reported', outdated: 'Needs review', disproven: 'Invalidated' } as const;

export function SituationReport({ incidentId, runId, incidentTime, onSelectEvent }: {
  incidentId: string; runId: string; incidentTime: number; onSelectEvent: (id: string) => void;
}) {
  const query = useQuery({ queryKey: ['situation-report', incidentId, runId],
    queryFn: ({ signal }) => api.getSituationReport(incidentId, signal), refetchInterval: 3000, retry: false });
  const history = useQuery({ queryKey: ['events', incidentId, runId],
    queryFn: ({ signal }) => api.getEvents(incidentId, signal), refetchInterval: 2000, retry: false });
  const retry = useMutation({ mutationFn: () => api.retrySituationReport(incidentId),
    onSuccess: () => { void query.refetch(); } });
  const data = query.data?.run_id === runId ? query.data : undefined;
  // Hide later evidence immediately on rewind, before the next server poll.
  const report = data?.known_through != null && data.known_through <= incidentTime && data.report?.events ? data.report : null;
  const events = history.data?.run_id === runId ? history.data.events : [];
  const updating = data?.state === 'queued' || data?.state === 'processing' || data?.state === 'retrying';
  const sections = [['overview', 'What happened'], ['scene_status', 'Latest reported situation'],
    ['developments', 'Key changes'], ['clarifications', 'Still unclear']] as const;
  const citedIds = report ? [...new Set(sections.flatMap(([key]) => report.sections[key].flatMap((item) => item.event_ids)))] : [];
  return <section className="panel personnel-sitrep" aria-labelledby="sitrep-heading">
    <div className="section-heading"><div><h2 id="sitrep-heading"><span className="section-number">02</span> Scene summary</h2>
      <p>A quick sitrep for arriving responders. Select a citation to review the event.</p></div>
      <span className="status" role="status">{data?.state === 'retrying' ? 'Retry scheduled' : data?.state === 'failed' ? 'Summary unavailable' : updating ? 'Updating summary' : report ? 'Summary available' : 'Awaiting events'}</span></div>
    {report && <>
      <div className="sitrep-meta"><p>Sources through <strong>{formatTime(data!.known_through!)}</strong></p>
        <div className="sitrep-legend" aria-label="Citation status legend">
          {Object.entries(labels).map(([status, label]) => <span key={status} className={`sitrep-key citation-${status}`}>{label}</span>)}
        </div></div>
      <p className="sitrep-caption">Citation colors reflect event assessments, not verified confidence. Reported claims may still be uncertain.</p>
      {data?.stale && <p className="notice" role="status">Events have changed or analysis is pending. This summary may be out of date; citation labels reflect the available event list.</p>}
    </>}
    {!!data?.awaiting_analysis && <p className="sitrep-caption">{data.awaiting_analysis} transcript segment(s) awaiting event analysis.</p>}
    {(query.isError || retry.isError || data?.error) && <div role="alert" className="error">
      <p>{data?.error || messageFor(query.error || retry.error)}</p>
      {report && <p>The previous summary remains available below.</p>}
      <button disabled={retry.isPending} onClick={() => query.isError ? void query.refetch() : retry.mutate()}>Retry summary</button></div>}
    {!report && <p className="empty-state" data-testid="analysis-empty-state">
      {data?.state === 'disabled' ? 'Scene summaries are unavailable until analysis is configured.'
        : data?.error ? 'No summary is available at this point in the incident.'
        : updating ? 'Preparing a concise summary from the event list. It will appear automatically.'
          : 'Awaiting analyzed events. A short, source-linked scene summary will appear here automatically.'}</p>}
    {report && <div className="sitrep-sections">{sections.filter(([key]) => report.sections[key].length > 0).map(([key, title]) =>
      <section key={key} className={`sitrep-section sitrep-${key}`} aria-label={title}><h3>{title}</h3>
        <p>{report.sections[key].map((item, index) => <span className="sitrep-sentence" key={index}>{item.text}{' '}
          {item.event_ids.map((id) => {
            const live = events.find((event) => event.id === id);
            const event = live ?? report.events[id];
            if (!event) return null;
            const label = labels[event.status];
            return <button key={id} className={`sitrep-citation citation-${event.status}`} disabled={!live}
              title={`${label} · ${formatTime(event.timestamp_seconds)} · ${event.title}${event.status_reason ? ` · ${event.status_reason}` : ''}`}
              aria-label={`Event ${citedIds.indexOf(id) + 1}: ${label}. ${event.title}`}
              onClick={() => onSelectEvent(id)}>{citedIds.indexOf(id) + 1} · {label}</button>;
          })}{' '}</span>)}</p>
      </section>)}</div>}
    {report && !citedIds.length && <p className="empty-state">No supported scene summary yet.</p>}
  </section>;
}
