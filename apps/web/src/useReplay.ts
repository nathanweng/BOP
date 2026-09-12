import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, messageFor } from './api';
import { acceptClockSample, incidentPosition } from './clock';
import type { ClockSample, Playback, PlaybackAction, PlaybackCommand } from './types';

export function useReplay(incidentId: string, initial: Playback) {
  const queryClient = useQueryClient();
  const [sample, setSample] = useState<ClockSample>(() => ({ playback: initial, receivedAt: performance.now() }));
  const sampleRef = useRef(sample);
  const [now, setNow] = useState(() => performance.now());
  const [holdReason, setHoldReason] = useState<string | null>(null);
  const [holdPosition, setHoldPosition] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pausingForFault, setPausingForFault] = useState(false);
  const pauseInFlight = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const accept = useCallback((incoming: ClockSample) => {
    if (!mounted.current) return;
    setSample((current) => {
      const accepted = acceptClockSample(current, incoming);
      sampleRef.current = accepted;
      return accepted;
    });
    setNow(performance.now());
  }, []);

  const playbackQuery = useQuery({
    queryKey: ['playback', incidentId],
    queryFn: ({ signal }) => api.getPlayback(incidentId, signal),
    refetchInterval: 1000,
    retry: false,
    networkMode: 'always',
  });

  useEffect(() => {
    accept({ playback: initial, receivedAt: performance.now() });
  }, [initial, accept]);

  useEffect(() => {
    if (playbackQuery.data) accept(playbackQuery.data);
  }, [playbackQuery.data, accept]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, []);

  const command = useMutation({
    mutationFn: (input: PlaybackCommand) => api.controlPlayback(incidentId, input, sampleRef.current.playback.revision),
    onSuccess: (next) => {
      accept(next);
      queryClient.setQueryData(['playback', incidentId], next);
      void queryClient.invalidateQueries({ queryKey: ['incident', incidentId] });
    },
  });

  const pauseAfterFault = useCallback(async () => {
    if (pauseInFlight.current) return;
    pauseInFlight.current = true;
    setPausingForFault(true);
    try {
      // Read the latest revision before a safety pause: another tab may have
      // changed the clock while this browser was loading or disconnected.
      let fresh = await api.getPlayback(incidentId);
      accept(fresh);
      if (fresh.playback.state === 'playing') {
        try {
          fresh = await api.controlPlayback(incidentId, { action: 'pause' }, fresh.playback.revision);
        } catch (failure) {
          if (!(failure instanceof ApiError) || failure.status !== 409) throw failure;
          fresh = await api.getPlayback(incidentId);
          if (fresh.playback.state === 'playing') {
            fresh = await api.controlPlayback(incidentId, { action: 'pause' }, fresh.playback.revision);
          }
        }
      }
      accept(fresh);
      queryClient.setQueryData(['playback', incidentId], fresh);
      if (mounted.current) setError(null);
    } catch (failure) {
      if (mounted.current) setError(`Could not confirm that the server paused. Its clock may still be running. ${messageFor(failure)}`);
    } finally {
      pauseInFlight.current = false;
      if (mounted.current) setPausingForFault(false);
    }
  }, [accept, incidentId, queryClient]);

  const halt = useCallback((reason: string) => {
    setHoldPosition((current) => current ?? incidentPosition(sampleRef.current, performance.now()));
    setHoldReason((current) => current ?? reason);
    void pauseAfterFault();
  }, [pauseAfterFault]);

  useEffect(() => {
    if (playbackQuery.isError) {
      halt('Replay connection lost. Videos are paused locally. Reconnect before continuing.');
    }
  }, [playbackQuery.isError, halt]);

  useEffect(() => {
    if (holdReason && playbackQuery.data?.playback.state === 'playing' && !playbackQuery.isError) {
      void pauseAfterFault();
    }
  }, [holdReason, playbackQuery.data, playbackQuery.isError, pauseAfterFault]);

  const control = async (input: PlaybackAction | PlaybackCommand) => {
    const request: PlaybackCommand = typeof input === 'string' ? { action: input } as PlaybackCommand : input;
    setError(null);
    try {
      await command.mutateAsync(request);
      if (request.action !== 'pause') { setHoldReason(null); setHoldPosition(null); }
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        void playbackQuery.refetch();
        void queryClient.invalidateQueries({ queryKey: ['incident', incidentId] });
      }
    }
  };

  return {
    playback: sample.playback,
    // A disconnected browser cannot claim that its displayed cutoff is current.
    // Freeze locally until a confirmed server pause provides a reliable cutoff.
    position: holdPosition !== null && sample.playback.state === 'playing' ? holdPosition : incidentPosition(sample, now),
    playing: sample.playback.state === 'playing' && !holdReason && !playbackQuery.isError,
    connected: !playbackQuery.isError && !playbackQuery.isPending,
    pending: command.isPending || pausingForFault,
    holdReason,
    error,
    control,
    halt,
    reconnect: () => { void playbackQuery.refetch(); },
  };
}
