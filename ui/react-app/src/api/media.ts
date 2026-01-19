import apiClient from './client';

export interface SignedUrlRequest {
  source_type: 'podcast' | 'document';
  source_id: number;
}

export interface SignedUrlResponse {
  signed_url: string;
  expires_in_seconds: number;
}

export interface CacheStats {
  total_entries: number;
  expired_entries: number;
  valid_entries: number;
}

export const mediaApi = {
  // Get signed URL for podcast audio or document
  getSignedUrl: async (request: SignedUrlRequest): Promise<SignedUrlResponse> => {
    const response = await apiClient.post('/media/signed-url', request);
    return response.data;
  },

  // Get cache statistics
  getCacheStats: async (): Promise<CacheStats> => {
    const response = await apiClient.get('/media/cache-stats');
    return response.data;
  },

  // Clear cache
  clearCache: async (): Promise<{ cleared_entries: number }> => {
    const response = await apiClient.post('/media/clear-cache');
    return response.data;
  },

  // Get proxy URL for document (streams through backend)
  getProxyDocumentUrl: (documentId: number): string => {
    return `${apiClient.defaults.baseURL}/media/proxy/document/${documentId}`;
  },

  // Get proxy URL for podcast (streams through backend)
  getProxyPodcastUrl: (episodeId: number): string => {
    return `${apiClient.defaults.baseURL}/media/proxy/podcast/${episodeId}`;
  },
};
