import apiClient from './client';
import { PodcastSearchResult, PodcastEpisode, EpisodeListResponse, IngestionTask } from '../types';

export const podcastsApi = {
  // Search podcasts via Podcast Index API
  search: async (query: string): Promise<PodcastSearchResult[]> => {
    const response = await apiClient.post('/podcasts/search', { query });
    return response.data.results;
  },

  // Get episodes from a podcast feed with offset-based pagination
  getEpisodes: async (
    feedId: number,
    limit = 20,
    offset = 0
  ): Promise<EpisodeListResponse> => {
    const params: Record<string, number> = { limit, offset };
    const response = await apiClient.get(`/podcasts/episodes/${feedId}`, { params });
    return response.data;
  },

  // Select and save episodes to database
  selectEpisodes: async (feedId: number, episodeIds: number[], podcastName: string): Promise<PodcastEpisode[]> => {
    const response = await apiClient.post('/podcasts/select', {
      feed_id: feedId,
      episode_ids: episodeIds,
      podcast_name: podcastName,
    });
    // Backend returns array directly, not wrapped in { episodes: [...] }
    return response.data;
  },

  // List saved episodes (backend returns array directly)
  listSaved: async (): Promise<PodcastEpisode[]> => {
    const response = await apiClient.get('/podcasts/');
    // Backend returns List[PodcastEpisodeDB] directly, not wrapped
    return response.data;
  },

  // Download audio for an episode
  downloadAudio: async (episodeId: number): Promise<{ success: boolean; message: string }> => {
    const response = await apiClient.post(`/podcasts/download/${episodeId}`);
    return response.data;
  },

  // Get a single episode by ID (for polling status)
  getEpisode: async (episodeId: number): Promise<PodcastEpisode> => {
    const response = await apiClient.get(`/podcasts/${episodeId}`);
    return response.data;
  },

  // Process podcast episode (download → transcribe → index)
  processPodcast: async (episodeId: number): Promise<IngestionTask> => {
    const response = await apiClient.post(`/podcasts/process/${episodeId}`);
    return response.data;
  },

  // Get processing status
  getProcessingStatus: async (taskId: string): Promise<IngestionTask> => {
    const response = await apiClient.get(`/podcasts/process/status/${taskId}`);
    return response.data;
  },

  // Check if episode has been analyzed
  checkAnalyzed: async (episodeId: number): Promise<{ analyzed: boolean; insight_count: number }> => {
    const response = await apiClient.get(`/podcasts/${episodeId}/analyzed`);
    return response.data;
  },

  // Get episode processing status
  getEpisodeProcessingStatus: async (episodeId: number): Promise<{ status: string }> => {
    const response = await apiClient.get(`/podcasts/${episodeId}/processing-status`);
    return response.data;
  },
};
