import apiClient from './client';
import { IngestionTask, TaskStatus } from '../types';

export const ingestionApi = {
  // Start transcription for a podcast episode
  transcribe: async (episodeId: number): Promise<IngestionTask> => {
    const response = await apiClient.post(`/ingestion/transcribe/${episodeId}`);
    return response.data;
  },

  // Run vector indexing pipeline
  runIndexing: async (options?: {
    podcastsOnly?: boolean;
    documentsOnly?: boolean;
    withSpeakerIdentification?: boolean;
  }): Promise<IngestionTask> => {
    const response = await apiClient.post('/ingestion/index', {
      podcasts_only: options?.podcastsOnly || false,
      documents_only: options?.documentsOnly || false,
      with_speaker_identification: options?.withSpeakerIdentification ?? true,
    });
    return response.data;
  },

  // Get task status
  getStatus: async (taskId: string): Promise<IngestionTask> => {
    const response = await apiClient.get(`/ingestion/status/${taskId}`);
    return response.data;
  },

  // List all ingestion tasks
  listTasks: async (status?: TaskStatus): Promise<IngestionTask[]> => {
    const params: Record<string, string> = {};
    if (status) params.status = status;
    const response = await apiClient.get('/ingestion/tasks', { params });
    return response.data;
  },

  // Delete a task
  deleteTask: async (taskId: string): Promise<void> => {
    await apiClient.delete(`/ingestion/tasks/${taskId}`);
  },
};
