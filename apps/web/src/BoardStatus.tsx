import { useQuery } from '@tanstack/react-query';
import { api } from './api';

export function BoardStatus({ incidentId, runId }: { incidentId: string; runId: string }) {
  const query = useQuery({ queryKey: ['knowledge', incidentId, runId],
    queryFn: ({ signal }) => api.getKnowledge(incidentId, signal), refetchInterval: 5000, retry: false });
  const data = query.data?.run_id === runId ? query.data : undefined;
  if (!data) return null;
  const busy = data.state === 'processing' || data.state === 'queued';
  return <span className="board-status board-launch-status" role="status"><i className={busy ? 'working' : ''} />
    {busy ? 'Preparing board…' : data.state === 'failed' ? 'Board needs attention' : data.version_id ? 'Board ready' : 'Board awaiting transcripts'}</span>;
}
