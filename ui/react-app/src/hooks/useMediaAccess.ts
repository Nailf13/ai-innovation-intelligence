import { useQuery, UseQueryResult } from '@tanstack/react-query';
import { mediaApi, SignedUrlResponse } from '../api/media';
import { podcastsApi } from '../api/podcasts';
import { documentsApi } from '../api/documents';
import { PodcastEpisode, Document } from '../types';

// Hook to get signed URL with caching.
// Fails silently — callers should always have a proxy URL fallback.
export function useSignedUrl(
  sourceType: 'podcast' | 'document',
  sourceId: number | null,
  enabled: boolean = true
): UseQueryResult<SignedUrlResponse, Error> {
  return useQuery({
    queryKey: ['signed-url', sourceType, sourceId],
    queryFn: () => {
      if (!sourceId) throw new Error('Source ID is required');
      return mediaApi.getSignedUrl({ source_type: sourceType, source_id: sourceId });
    },
    enabled: enabled && sourceId !== null,
    staleTime: 40 * 60 * 1000, // 40 minutes (before 45-min server cache expires)
    gcTime: 50 * 60 * 1000, // 50 minutes
    retry: false, // Don't retry — fall back to proxy if signed URLs fail
  });
}

// Hook to get podcast episode details
export function usePodcastEpisode(episodeId: number | null): UseQueryResult<PodcastEpisode, Error> {
  return useQuery({
    queryKey: ['podcast', episodeId],
    queryFn: () => {
      if (!episodeId) throw new Error('Episode ID is required');
      return podcastsApi.getEpisode(episodeId);
    },
    enabled: episodeId !== null,
    staleTime: 5 * 60 * 1000, // 5 minutes
    gcTime: 10 * 60 * 1000, // 10 minutes
  });
}

// Hook to get document details
export function useDocument(documentId: number | null): UseQueryResult<Document, Error> {
  return useQuery({
    queryKey: ['document', documentId],
    queryFn: () => {
      if (!documentId) throw new Error('Document ID is required');
      return documentsApi.getDocument(documentId);
    },
    enabled: documentId !== null,
    staleTime: 5 * 60 * 1000, // 5 minutes
    gcTime: 10 * 60 * 1000, // 10 minutes
  });
}
