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
  Download,
  CheckCircle,
  Plus,
  X,
  ExternalLink,
  Calendar,
  Clock,
  Loader2,
  ChevronDown,
  Trash2,
} from 'lucide-react';
import { format } from 'date-fns';
import clsx from 'clsx';

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
  // Track episode statuses locally for real-time updates
  const [episodeStatuses, setEpisodeStatuses] = useState<Map<number, string>>(new Map());

  // SSE connection for real-time episode status updates
  useSSE({
    url: `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/podcasts/events`,
    enabled: true,
    onMessage: (event) => {
      console.log('SSE event received:', event);

      if (event.type === 'episode_status') {
        const { episode_id, status } = event;

        // Update local status map
        setEpisodeStatuses((prev) => {
          const next = new Map(prev);
          next.set(episode_id, status);
          return next;
        });

        // Refresh the podcasts list when status changes
        queryClient.invalidateQueries({ queryKey: ['podcasts', 'saved'] });
      }

      if (event.type === 'analysis_status') {
        // Refresh on analysis completion
        if (event.status === 'completed' || event.status === 'failed') {
          queryClient.invalidateQueries({ queryKey: ['podcasts', 'saved'] });
          queryClient.invalidateQueries({ queryKey: ['analysis', 'tasks'] });
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
  const { data: savedPodcasts = [], isLoading: loadingSaved } = useQuery({
    queryKey: ['podcasts', 'saved'],
    queryFn: podcastsApi.listSaved,
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
    },
  });

  // Delete episode mutation
  const deleteMutation = useMutation({
    mutationFn: (episodeId: number) => podcastsApi.deleteEpisode(episodeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['podcasts', 'saved'] });
    },
  });

  // Process episode handler (starts download, transcription, and indexing)
  const handleProcess = async (episodeId: number) => {
    try {
      // The processing endpoint handles download, transcription, and indexing
      // SSE will provide real-time status updates
      await podcastsApi.processPodcast(episodeId);
    } catch (error) {
      console.error('Error starting processing:', error);
    }
  };

  // Retry indexing handler (for failed episodes with transcripts)
  const handleRetryIndexing = async (episodeId: number) => {
    try {
      // Retry indexing only - transcript already exists
      // SSE will provide real-time status updates
      await podcastsApi.retryIndexing(episodeId);
    } catch (error) {
      console.error('Error retrying indexing:', error);
      alert('Failed to retry indexing. Please try again.');
    }
  };

  // Delete episode handler
  const handleDelete = async (episodeId: number, episodeTitle: string) => {
    if (window.confirm(`Are you sure you want to delete "${episodeTitle}"? This will also delete all associated insights.`)) {
      try {
        await deleteMutation.mutateAsync(episodeId);
      } catch (error) {
        console.error('Error deleting episode:', error);
        alert('Failed to delete episode. Please try again.');
      }
    }
  };

  // Get episode status from local map or episode data
  const getEpisodeStatus = (episode: PodcastEpisode): string => {
    // Check SSE-updated status first
    const sseStatus = episodeStatuses.get(episode.id);
    if (sseStatus) return sseStatus;

    // Fallback to episode status field if available
    if ('status' in episode) {
      return (episode as any).status;
    }

    // Legacy fallback logic
    if (episode.transcript_path) return 'ready';
    if (episode.audio_path) return 'transcribing';
    return 'needs_processing';
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

  // Status badge component
  const StatusBadge = ({ status }: { status: string }) => {
    const statusConfig: Record<string, { icon: any; label: string; color: string; spinning?: boolean }> = {
      needs_processing: {
        icon: Download,
        label: 'Needs Processing',
        color: 'text-gray-500',
      },
      downloading: {
        icon: Loader2,
        label: 'Downloading',
        color: 'text-blue-500',
        spinning: true,
      },
      transcribing: {
        icon: Loader2,
        label: 'Transcribing',
        color: 'text-yellow-500',
        spinning: true,
      },
      indexing: {
        icon: Loader2,
        label: 'Indexing',
        color: 'text-orange-500',
        spinning: true,
      },
      ready: {
        icon: CheckCircle,
        label: 'Ready for Analysis',
        color: 'text-green-500',
      },
      analyzing: {
        icon: Loader2,
        label: 'Analyzing',
        color: 'text-purple-500',
        spinning: true,
      },
      analyzed: {
        icon: CheckCircle,
        label: 'Analyzed',
        color: 'text-green-600',
      },
      failed: {
        icon: X,
        label: 'Failed',
        color: 'text-red-500',
      },
    };

    const config = statusConfig[status] || statusConfig.needs_processing;
    const Icon = config.icon;

    return (
      <span className={clsx('flex items-center gap-1 text-sm', config.color)}>
        <Icon className={clsx('w-4 h-4', config.spinning && 'animate-spin')} />
        {config.label}
      </span>
    );
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
              <div
                key={podcast.id}
                className="card p-4 hover:shadow-md transition-shadow"
              >
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
                      <StatusBadge status={getEpisodeStatus(podcast)} />
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {(() => {
                      const status = getEpisodeStatus(podcast);
                      const isProcessing = ['downloading', 'transcribing', 'indexing', 'analyzing'].includes(status);
                      const hasTranscript = !!podcast.gcs_transcript_uri;

                      return (
                        <>
                          {/* Show "Process" button for episodes that need processing */}
                          {status === 'needs_processing' && (
                            <button
                              onClick={() => handleProcess(podcast.id)}
                              className="btn btn-sm btn-primary flex items-center gap-1"
                            >
                              <Play className="w-4 h-4" />
                              Process
                            </button>
                          )}

                          {/* Show "Retry Indexing" button for failed episodes with transcripts */}
                          {status === 'failed' && hasTranscript && (
                            <button
                              onClick={() => handleRetryIndexing(podcast.id)}
                              className="btn btn-sm btn-primary flex items-center gap-1"
                            >
                              <Play className="w-4 h-4" />
                              Retry Indexing
                            </button>
                          )}

                          {/* Show "Process" button for failed episodes without transcripts */}
                          {status === 'failed' && !hasTranscript && (
                            <button
                              onClick={() => handleProcess(podcast.id)}
                              className="btn btn-sm btn-primary flex items-center gap-1"
                            >
                              <Play className="w-4 h-4" />
                              Retry Processing
                            </button>
                          )}

                          {/* Show processing indicator */}
                          {isProcessing && (
                            <span className="text-sm text-gray-500 flex items-center gap-1">
                              <Loader2 className="w-4 h-4 animate-spin" />
                              Processing...
                            </span>
                          )}

                          {/* Delete button - always show */}
                          <button
                            onClick={() => handleDelete(podcast.id, podcast.episode_title)}
                            disabled={isProcessing || deleteMutation.isPending}
                            className="btn btn-sm btn-ghost text-red-600 hover:bg-red-50 disabled:opacity-50"
                            title="Delete episode"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </>
                      );
                    })()}
                  </div>
                </div>
              </div>
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
                                    {Math.floor(ep.duration / 60)}m
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
