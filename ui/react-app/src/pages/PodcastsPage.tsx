import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Header } from '../components/layout';
import { LoadingState, EmptyState } from '../components/common';
import { podcastsApi, analysisApi } from '../api';
import { PodcastEpisode, PodcastSearchResult, PodcastEpisodeInfo } from '../types';
import { useSSE } from '../hooks/useSSE';
import {
  Search,
  Mic,
  Play,
  CheckCircle,
  Plus,
  X,
  ExternalLink,
  Calendar,
  Clock,
  Loader2,
  ChevronDown,
} from 'lucide-react';
import { format } from 'date-fns';
import clsx from 'clsx';

// Format duration from seconds to HH:MM:SS or MM:SS
const formatDuration = (seconds: number | undefined): string => {
  if (!seconds) return '';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
};

export function PodcastsPage() {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<PodcastSearchResult[]>([]);
  const [selectedPodcast, setSelectedPodcast] = useState<PodcastSearchResult | null>(null);
  const [episodes, setEpisodes] = useState<PodcastEpisodeInfo[]>([]);
  const [selectedEpisodes, setSelectedEpisodes] = useState<Set<number>>(new Set());
  const [showSearchPanel, setShowSearchPanel] = useState(false);
  const [hasMoreEpisodes, setHasMoreEpisodes] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);

  // Connect to SSE for real-time status updates
  useSSE({
    url: 'http://localhost:8000/podcasts/events',
    onMessage: (event) => {
      if (event.type === 'episode_status') {
        console.log(`[SSE] Episode ${event.episode_id} status changed to ${event.status}`);

        // Invalidate and refetch the specific episode status query
        queryClient.invalidateQueries({
          queryKey: ['podcast', event.episode_id, 'status'],
          refetchType: 'active', // Refetch active queries immediately
        });

        // Also invalidate the saved podcasts list to ensure UI consistency
        queryClient.invalidateQueries({
          queryKey: ['podcasts', 'saved'],
          refetchType: 'active',
        });
      } else if (event.type === 'analysis_status') {
        console.log(`[SSE] Analysis task ${event.task_id} status: ${event.status}`);

        // Invalidate analysis-related queries
        queryClient.invalidateQueries({
          queryKey: ['analysis', 'tasks'],
          refetchType: 'active',
        });
        queryClient.invalidateQueries({
          queryKey: ['analysis', 'stats'],
          refetchType: 'active',
        });

        // Also invalidate all episode statuses when analysis completes or fails
        if (event.status === 'completed' || event.status === 'failed') {
          queryClient.invalidateQueries({
            queryKey: ['podcast'],
            refetchType: 'active',
          });
        }
      }
    },
    onError: (error) => {
      console.error('SSE connection error:', error);
    },
    onOpen: () => {
      console.log('SSE connection established');
    },
  });

  // Fetch saved podcasts
  const { data: rawSavedPodcasts = [], isLoading: loadingSaved } = useQuery({
    queryKey: ['podcasts', 'saved'],
    queryFn: podcastsApi.listSaved,
  });

  // Sort podcasts by created_at (newest first)
  const savedPodcasts = [...rawSavedPodcasts].sort((a, b) => {
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  // Search mutation
  const searchMutation = useMutation({
    mutationFn: podcastsApi.search,
    onSuccess: (data) => {
      setSearchResults(data);
      setIsSearching(false);
    },
    onError: () => {
      setIsSearching(false);
    },
  });

  // Get episodes mutation (initial load)
  const getEpisodesMutation = useMutation({
    mutationFn: (feedId: number) => podcastsApi.getEpisodes(feedId, 20, 0),
    onSuccess: (data) => {
      setEpisodes(data.episodes);
      setHasMoreEpisodes(data.has_more);
      setNextOffset(data.next_offset ?? undefined);
    },
  });

  // Select episodes mutation
  const selectMutation = useMutation({
    mutationFn: ({ feedId, episodeIds, podcastName }: { feedId: number; episodeIds: number[]; podcastName: string }) =>
      podcastsApi.selectEpisodes(feedId, episodeIds, podcastName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['podcasts', 'saved'] });
      setSelectedPodcast(null);
      setEpisodes([]);
      setSelectedEpisodes(new Set());
      setShowSearchPanel(false);
      setHasMoreEpisodes(false);
      setNextOffset(undefined);
    },
  });

  // Run analysis mutation
  const analysisMutation = useMutation({
    mutationFn: () => analysisApi.run({ runExtraction: true, runDimensionAssessment: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analysis', 'tasks'] });
      // SSE will handle real-time status updates, so no need for manual polling
    },
  });

  // Process handler (download → transcribe → index)
  const handleProcess = async (episodeId: number) => {
    try {
      await podcastsApi.processPodcast(episodeId);
      // SSE will handle real-time status updates
    } catch (error) {
      console.error('Error starting processing:', error);
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      setIsSearching(true);
      searchMutation.mutate(searchQuery);
    }
  };

  const handleSelectPodcast = (podcast: PodcastSearchResult) => {
    setSelectedPodcast(podcast);
    setSelectedEpisodes(new Set());
    setEpisodes([]);
    setHasMoreEpisodes(false);
    setNextOffset(undefined);
    getEpisodesMutation.mutate(podcast.feed_id);
  };

  const handleLoadMoreEpisodes = async () => {
    if (!selectedPodcast || nextOffset === undefined || loadingMore) return;

    setLoadingMore(true);
    try {
      const data = await podcastsApi.getEpisodes(selectedPodcast.feed_id, 20, nextOffset);
      setEpisodes((prev) => [...prev, ...data.episodes]);
      setHasMoreEpisodes(data.has_more);
      setNextOffset(data.next_offset ?? undefined);
    } catch (error) {
      console.error('Error loading more episodes:', error);
    } finally {
      setLoadingMore(false);
    }
  };

  const handleToggleEpisode = (episodeId: number) => {
    setSelectedEpisodes((prev) => {
      const next = new Set(prev);
      if (next.has(episodeId)) {
        next.delete(episodeId);
      } else {
        next.add(episodeId);
      }
      return next;
    });
  };

  const handleAddEpisodes = () => {
    if (selectedPodcast && selectedEpisodes.size > 0) {
      selectMutation.mutate({
        feedId: selectedPodcast.feed_id,
        episodeIds: Array.from(selectedEpisodes),
        podcastName: selectedPodcast.title,
      });
    }
  };

  // Get episode status from backend (cached with React Query)
  // SSE updates will automatically invalidate these queries for real-time updates
  const useEpisodeStatus = (episodeId: number) => {
    return useQuery({
      queryKey: ['podcast', episodeId, 'status'],
      queryFn: () => podcastsApi.getEpisodeProcessingStatus(episodeId),
      // Refetch immediately when query is invalidated by SSE
      staleTime: 0, // Always consider data stale so invalidation triggers refetch
      refetchOnMount: true,
      refetchOnWindowFocus: false, // Don't refetch on window focus
    });
  };

  // Status helper functions based on backend status
  const getStatusInfo = (statusResponse: { status: string } | undefined) => {
    if (!statusResponse) return { status: 'loading', label: 'Loading...', needsAction: false, isProcessing: false };

    const { status } = statusResponse;

    switch (status) {
      case 'analyzed':
        return { status: 'analyzed', label: 'Analyzed', needsAction: false, isProcessing: false };
      case 'analyzing':
        return { status: 'analyzing', label: 'Analyzing...', needsAction: false, isProcessing: true };
      case 'ready':
        return { status: 'ready', label: 'Ready to analyze', needsAction: false, isProcessing: false };
      case 'downloading':
        return { status: 'downloading', label: 'Downloading...', needsAction: false, isProcessing: true };
      case 'transcribing':
        return { status: 'transcribing', label: 'Transcribing...', needsAction: false, isProcessing: true };
      case 'indexing':
        return { status: 'indexing', label: 'Indexing...', needsAction: false, isProcessing: true };
      case 'needs_processing':
        return { status: 'needs_processing', label: 'Needs processing', needsAction: true, isProcessing: false };
      default:
        return { status: 'unknown', label: 'Unknown', needsAction: false, isProcessing: false };
    }
  };

  return (
    <div className="h-full flex flex-col">
      <Header
        title="Podcasts"
        subtitle={`${savedPodcasts.length} episodes in database`}
      />

      <div className="flex-1 p-6 overflow-auto">
        {/* Action Bar */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setShowSearchPanel(true)}
              className="btn btn-primary flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Add Podcasts
            </button>
            <button
              onClick={() => analysisMutation.mutate()}
              disabled={analysisMutation.isPending || savedPodcasts.length === 0}
              className="btn btn-secondary flex items-center gap-2"
            >
              <Play className="w-4 h-4" />
              Run Pipeline
            </button>
          </div>

          {/* Filter */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Filter episodes..."
              className="input pl-10 w-64"
            />
          </div>
        </div>

        {/* Saved Podcasts List */}
        {loadingSaved ? (
          <LoadingState message="Loading podcasts..." />
        ) : savedPodcasts.length === 0 ? (
          <EmptyState
            icon={Mic}
            title="No podcasts yet"
            description="Add podcasts from the Podcast Index to start analyzing health trends."
            action={
              <button
                onClick={() => setShowSearchPanel(true)}
                className="btn btn-primary"
              >
                Add Podcasts
              </button>
            }
          />
        ) : (
          <div className="grid gap-4">
            {savedPodcasts.map((podcast) => (
              <PodcastCard
                key={podcast.id}
                podcast={podcast}
                onProcess={handleProcess}
                getStatusInfo={getStatusInfo}
                useEpisodeStatus={useEpisodeStatus}
              />
            ))}
          </div>
        )}
      </div>

      {/* Search Panel (Slide-over) */}
      {showSearchPanel && (
        <div className="fixed inset-0 z-50 flex">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setShowSearchPanel(false)}
          />
          <div className="absolute right-0 top-0 bottom-0 w-[600px] bg-white shadow-xl slide-in">
            <div className="h-full flex flex-col">
              {/* Panel Header */}
              <div className="flex items-center justify-between p-4 border-b">
                <h2 className="text-lg font-semibold">Add Podcasts</h2>
                <button
                  onClick={() => setShowSearchPanel(false)}
                  className="p-2 hover:bg-gray-100 rounded-lg"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Search Form */}
              <div className="p-4 border-b">
                <form onSubmit={handleSearch} className="flex gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search podcasts..."
                      className="input pl-10"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={isSearching}
                    className="btn btn-primary"
                  >
                    {isSearching ? 'Searching...' : 'Search'}
                  </button>
                </form>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-auto p-4">
                {selectedPodcast ? (
                  // Episodes View
                  <div>
                    <button
                      onClick={() => setSelectedPodcast(null)}
                      className="text-sm text-savencia-primary hover:underline mb-4"
                    >
                      ← Back to search results
                    </button>

                    <div className="mb-4">
                      <h3 className="font-medium">{selectedPodcast.title}</h3>
                      <p className="text-sm text-gray-500">
                        {selectedPodcast.author}
                      </p>
                    </div>

                    {getEpisodesMutation.isPending ? (
                      <LoadingState message="Loading episodes..." />
                    ) : (
                      <div className="space-y-2">
                        {episodes.map((ep) => (
                          <label
                            key={ep.id}
                            className={clsx(
                              'flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors',
                              selectedEpisodes.has(ep.id)
                                ? 'bg-savencia-primary/10 border border-savencia-primary'
                                : 'bg-gray-50 hover:bg-gray-100 border border-transparent'
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={selectedEpisodes.has(ep.id)}
                              onChange={() => handleToggleEpisode(ep.id)}
                              className="mt-1"
                            />
                            <div className="flex-1 min-w-0">
                              <p className="font-medium text-sm truncate">
                                {ep.title}
                              </p>
                              <div className="flex items-center gap-3 mt-1">
                                {ep.date_published && (
                                  <span className="text-xs text-gray-500 flex items-center gap-1">
                                    <Calendar className="w-3 h-3" />
                                    {format(new Date(ep.date_published), 'MMM d, yyyy')}
                                  </span>
                                )}
                                {ep.duration && (
                                  <span className="text-xs text-gray-500 flex items-center gap-1">
                                    <Clock className="w-3 h-3" />
                                    {formatDuration(ep.duration)}
                                  </span>
                                )}
                              </div>
                            </div>
                          </label>
                        ))}

                        {/* Load More Button */}
                        {hasMoreEpisodes && (
                          <button
                            onClick={handleLoadMoreEpisodes}
                            disabled={loadingMore}
                            className="w-full py-3 mt-4 text-sm text-savencia-primary hover:bg-savencia-primary/5 rounded-lg flex items-center justify-center gap-2 transition-colors"
                          >
                            {loadingMore ? (
                              <>
                                <Loader2 className="w-4 h-4 animate-spin" />
                                Loading more...
                              </>
                            ) : (
                              <>
                                <ChevronDown className="w-4 h-4" />
                                Load more episodes
                              </>
                            )}
                          </button>
                        )}
                      </div>
                    )}

                    {selectedEpisodes.size > 0 && (
                      <div className="sticky bottom-0 mt-4 p-4 bg-white border-t -mx-4">
                        <button
                          onClick={handleAddEpisodes}
                          disabled={selectMutation.isPending}
                          className="btn btn-primary w-full"
                        >
                          {selectMutation.isPending
                            ? 'Adding...'
                            : `Add ${selectedEpisodes.size} episode${selectedEpisodes.size > 1 ? 's' : ''}`}
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  // Search Results
                  <div className="space-y-3">
                    {searchResults.map((result) => (
                      <div
                        key={result.feed_id}
                        onClick={() => handleSelectPodcast(result)}
                        className="p-4 bg-gray-50 rounded-lg cursor-pointer hover:bg-gray-100 transition-colors"
                      >
                        <div className="flex items-start gap-3">
                          {result.image_url ? (
                            <img
                              src={result.image_url}
                              alt=""
                              className="w-12 h-12 rounded-lg object-cover"
                            />
                          ) : (
                            <div className="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                              <Mic className="w-6 h-6 text-purple-600" />
                            </div>
                          )}
                          <div className="flex-1 min-w-0">
                            <h4 className="font-medium text-sm truncate">
                              {result.title}
                            </h4>
                            <p className="text-xs text-gray-500 truncate">
                              {result.author}
                            </p>
                            {result.episode_count && (
                              <p className="text-xs text-gray-400 mt-1">
                                {result.episode_count} episodes
                              </p>
                            )}
                          </div>
                          <ExternalLink className="w-4 h-4 text-gray-400 flex-shrink-0" />
                        </div>
                      </div>
                    ))}

                    {searchResults.length === 0 && !isSearching && searchQuery && (
                      <EmptyState
                        title="No podcasts found"
                        description="Try a different search term"
                      />
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Separate component for podcast card that fetches its own status
interface PodcastCardProps {
  podcast: PodcastEpisode;
  onProcess: (id: number) => Promise<void>;
  getStatusInfo: (statusResponse: { status: string } | undefined) => {
    status: string;
    label: string;
    needsAction: boolean;
    isProcessing: boolean;
  };
  useEpisodeStatus: (episodeId: number) => {
    data: { status: string } | undefined;
    isLoading: boolean;
  };
}

function PodcastCard({ podcast, onProcess, getStatusInfo, useEpisodeStatus }: PodcastCardProps) {
  const { data: statusData, isLoading: statusLoading } = useEpisodeStatus(podcast.id);
  const statusInfo = getStatusInfo(statusData);

  const handleProcessClick = async () => {
    await onProcess(podcast.id);
    // SSE will automatically update status in real-time
  };

  // Determine if button should be shown
  const shouldShowProcessButton = statusInfo.needsAction && !statusInfo.isProcessing;

  // Status icon and color
  const getStatusIcon = () => {
    switch (statusInfo.status) {
      case 'analyzed':
        return <CheckCircle className="w-4 h-4 text-blue-500" />;
      case 'analyzing':
        return <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />;
      case 'ready':
        return <CheckCircle className="w-4 h-4 text-green-500" />;
      case 'downloading':
      case 'transcribing':
      case 'indexing':
        return <Loader2 className="w-4 h-4 text-yellow-500 animate-spin" />;
      case 'needs_processing':
        return <Clock className="w-4 h-4 text-gray-400" />;
      default:
        return <Clock className="w-4 h-4 text-gray-400" />;
    }
  };

  return (
    <div className="card p-4 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Mic className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <h3 className="font-medium text-gray-900">
                {podcast.episode_title}
              </h3>
              <p className="text-sm text-gray-500">
                {podcast.podcast_name}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4 text-sm text-gray-500">
            {podcast.episode_date && (
              <span className="flex items-center gap-1">
                <Calendar className="w-4 h-4" />
                {format(new Date(podcast.episode_date), 'MMM d, yyyy')}
              </span>
            )}
            <span className="flex items-center gap-1">
              {statusLoading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading status...
                </>
              ) : (
                <>
                  {getStatusIcon()}
                  {statusInfo.label}
                </>
              )}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {shouldShowProcessButton && (
            <button
              onClick={handleProcessClick}
              className="btn btn-sm btn-primary flex items-center gap-1"
            >
              <Play className="w-4 h-4" />
              Process
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
