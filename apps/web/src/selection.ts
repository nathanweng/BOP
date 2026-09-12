import { create } from 'zustand';

// Selection is UI state only. The API remains the owner of incident/run timing.
export const useSelection = create<{
  selectedByIncident: Record<string, string>;
  select: (incidentId: string, recordingId: string) => void;
}>((set) => ({
  selectedByIncident: {},
  select: (incidentId, recordingId) => set((state) => ({
    selectedByIncident: { ...state.selectedByIncident, [incidentId]: recordingId },
  })),
}));
