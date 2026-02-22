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

// Circuit breaker: stop calling signed-url endpoint after a 503 (service unavailable)
let _signedUrlUnavailableUntil = 0;
const CIRCUIT_BREAKER_MS = 10 * 60 * 1000; // 10 minutes

export const mediaApi = {
  // Get signed URL for podcast audio or document
  getSignedUrl: async (request: SignedUrlRequest): Promise<SignedUrlResponse> => {
    // Circuit breaker — skip if the service recently returned 503
    if (Date.now() < _signedUrlUnavailableUntil) {
      throw new Error('Signed URLs unavailable (circuit breaker open)');
    }
    try {
      const response = await apiClient.post('/media/signed-url', request);
      return response.data;
    } catch (err: any) {
      if (err?.response?.status === 503) {
        _signedUrlUnavailableUntil = Date.now() + CIRCUIT_BREAKER_MS;
      }
      throw err;
    }
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
