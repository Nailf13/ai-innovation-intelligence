import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Header } from '../components/layout';
import { LoadingState, EmptyState } from '../components/common';
import { podcastsApi, analysisApi, ingestionApi } from '../api';
import { PodcastEpisode, PodcastSearchResult, PodcastEpisodeInfo } from '../types';
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
  const [downloadingIds, setDownloadingIds] = useState<Set<number>>(new Set());
  const [transcribingIds, setTranscribingIds] = useState<Set<number>>(new Set());
  const pollingIntervals = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const transcriptionPolling = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());

  // Cleanup polling intervals on unmount
  useEffect(() => {
    return () => {
      pollingIntervals.current.forEach((interval) => clearInterval(interval));
      transcriptionPolling.current.forEach((interval) => clearInterval(interval));
    };
  }, []);

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

  // Start polling for download completion
  const startDownloadPolling = (episodeId: number) => {
    // Clear any existing interval for this episode
    const existing = pollingIntervals.current.get(episodeId);
    if (existing) clearInterval(existing);

    const interval = setInterval(async () => {
      try {
        const episode = await podcastsApi.getEpisode(episodeId);
        if (episode.audio_path) {
          // Download complete, stop polling and update UI
          clearInterval(interval);
          pollingIntervals.current.delete(episodeId);
          setDownloadingIds((prev) => {
            const next = new Set(prev);
            next.delete(episodeId);
            return next;
          });
          // Refresh the podcasts list to show updated status
          queryClient.invalidateQueries({ queryKey: ['podcasts', 'saved'] });
        }
      } catch (error) {
        console.error('Error polling episode status:', error);
      }
    }, 2000); // Poll every 2 seconds

    pollingIntervals.current.set(episodeId, interval);

    // Stop polling after 5 minutes to avoid infinite polling
    setTimeout(() => {
      const int = pollingIntervals.current.get(episodeId);
      if (int) {
        clearInterval(int);
        pollingIntervals.current.delete(episodeId);
        setDownloadingIds((prev) => {
          const next = new Set(prev);
          next.delete(episodeId);
          return next;
        });
      }
    }, 5 * 60 * 1000);
  };

  // Download audio handler
  const handleDownload = async (episodeId: number) => {
    setDownloadingIds((prev) => new Set(prev).add(episodeId));
    try {
      await podcastsApi.downloadAudio(episodeId);
      // Start polling for completion
      startDownloadPolling(episodeId);
    } catch (error) {
      console.error('Error starting download:', error);
      setDownloadingIds((prev) => {
        const next = new Set(prev);
        next.delete(episodeId);
        return next;
      });
    }
  };

  // Start polling for transcription completion
  const startTranscriptionPolling = (episodeId: number, taskId: string) => {
    // Clear any existing interval for this episode
    const existing = transcriptionPolling.current.get(episodeId);
    if (existing) clearInterval(existing);

    const interval = setInterval(async () => {
      try {
        const status = await ingestionApi.getStatus(taskId);
        if (status.status === 'completed' || status.status === 'failed') {
          // Transcription finished, stop polling and update UI
          clearInterval(interval);
          transcriptionPolling.current.delete(episodeId);
          setTranscribingIds((prev) => {
            const next = new Set(prev);
            next.delete(episodeId);
            return next;
          });
          // Refresh the podcasts list to show updated status
          queryClient.invalidateQueries({ queryKey: ['podcasts', 'saved'] });
        }
      } catch (error) {
        console.error('Error polling transcription status:', error);
      }
    }, 3000); // Poll every 3 seconds

    transcriptionPolling.current.set(episodeId, interval);

    // Stop polling after 30 minutes (transcription can take a while)
    setTimeout(() => {
      const int = transcriptionPolling.current.get(episodeId);
      if (int) {
        clearInterval(int);
        transcriptionPolling.current.delete(episodeId);
        setTranscribingIds((prev) => {
          const next = new Set(prev);
          next.delete(episodeId);
          return next;
        });
      }
    }, 30 * 60 * 1000);
  };

  // Transcribe audio handler
  const handleTranscribe = async (episodeId: number) => {
    setTranscribingIds((prev) => new Set(prev).add(episodeId));
    try {
      const task = await ingestionApi.transcribe(episodeId);
      // Start polling for completion
      startTranscriptionPolling(episodeId, task.task_id);
    } catch (error) {
      console.error('Error starting transcription:', error);
      setTranscribingIds((prev) => {
        const next = new Set(prev);
        next.delete(episodeId);
        return next;
      });
    }
  };

  const isTranscribing = (episodeId: number) => transcribingIds.has(episodeId);

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

  const hasTranscript = (podcast: PodcastEpisode) => !!podcast.gcs_transcript_uri;
  const hasAudio = (podcast: PodcastEpisode) => !!podcast.gcs_audio_uri;
  const isDownloading = (episodeId: number) => downloadingIds.has(episodeId);

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
              Run Analysis
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
                      <span className="flex items-center gap-1">
                        {hasTranscript(podcast) ? (
                          <>
                            <CheckCircle className="w-4 h-4 text-green-500" />
                            Transcribed
                          </>
                        ) : hasAudio(podcast) ? (
                          <>
                            <Clock className="w-4 h-4 text-yellow-500" />
                            Pending transcription
                          </>
                        ) : (
                          <>
                            <Download className="w-4 h-4 text-gray-400" />
                            Needs download
                          </>
                        )}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {!hasAudio(podcast) && podcast.audio_url && (
                      <button
                        onClick={() => handleDownload(podcast.id)}
                        disabled={isDownloading(podcast.id)}
                        className="btn btn-sm btn-secondary flex items-center gap-1"
                      >
                        {isDownloading(podcast.id) ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Downloading...
                          </>
                        ) : (
                          <>
                            <Download className="w-4 h-4" />
                            Download
                          </>
                        )}
                      </button>
                    )}
                    {hasAudio(podcast) && !hasTranscript(podcast) && (
                      <button
                        onClick={() => handleTranscribe(podcast.id)}
                        disabled={isTranscribing(podcast.id)}
                        className="btn btn-sm btn-primary flex items-center gap-1"
                      >
                        {isTranscribing(podcast.id) ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Transcribing...
                          </>
                        ) : (
                          'Transcribe'
                        )}
                      </button>
                    )}
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
                              {ep.date_published && (
                                <p className="text-xs text-gray-500">
                                  {format(new Date(ep.date_published), 'MMM d, yyyy')}
                                </p>
                              )}
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
