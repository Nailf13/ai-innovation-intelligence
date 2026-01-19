import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Header } from '../components/layout';
import {
  LoadingState,
  EmptyState,
} from '../components/common';
import { ScatterPlotVisualization } from '../components/ScatterPlotVisualization';
import { NetworkVisualization } from '../components/NetworkVisualization';
import { DetailPanel } from '../components/DetailPanel';
import { VideoModal } from '../components/VideoModal';
import { PdfModal } from '../components/PdfModal';
import { insightsApi } from '../api';
import { InsightHierarchy, VisualizationData } from '../types';
import {
  Sparkles,
  TrendingUp,
  AlertCircle,
  BarChart3,
  Network,
  RefreshCw,
} from 'lucide-react';
import clsx from 'clsx';

// ============================================
// Types
// ============================================

type SelectionType = 'cluster' | 'macro' | 'unit' | null;

interface Selection {
  type: SelectionType;
  id: number;
}

// ============================================
// Color Constants
// ============================================

const TYPE_COLORS = {
  trend: '#3b82f6',
  health_stake: '#f97316',
  cluster: '#d1e3f5',
  macro: '#22c55e',
};

const STAGE_COLORS: Record<string, string> = {
  'Nascent experimentation': '#c4b5fd',
  'Early adoption': '#93c5fd',
  'Crossing the chasm': '#41f1ce',
  'Established practice': '#fcd34d',
};

// ============================================
// Main Page Component
// ============================================

export function InsightsPage() {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [viewMode, setViewMode] = useState<'network' | 'trends' | 'stakes'>('network');
  const [videoModal, setVideoModal] = useState<{
    open: boolean;
    url?: string;
    start?: number;
    end?: number;
    title?: string;
    subtitle?: string;
  }>({ open: false });
  const [pdfModal, setPdfModal] = useState<{
    open: boolean;
    pdfUrl?: string;
    initialPage?: number;
    title?: string;
  }>({ open: false });

  // Fetch hierarchy
  const { data: hierarchy, isLoading, refetch: refetchHierarchy } = useQuery({
    queryKey: ['insights', 'hierarchy'],
    queryFn: insightsApi.getHierarchy,
  });

  // Function to clear sparkles from localStorage
  const clearSparkles = useCallback(() => {
    console.log('🧹 Clearing all sparkles from localStorage');
    localStorage.removeItem('network-viz-sparkles');
    localStorage.removeItem('network-viz-previous-ids');
  }, []);

  // Fetch visualization data
  const { data: vizData, isLoading: isLoadingViz } = useQuery({
    queryKey: ['insights', 'visualization'],
    queryFn: insightsApi.getVisualizationData,
    enabled: viewMode !== 'network',
  });

  // Fetch selected item details
  const { data: selectedCluster } = useQuery({
    queryKey: ['insights', 'cluster', selection?.id],
    queryFn: () => insightsApi.getCluster(selection!.id),
    enabled: selection?.type === 'cluster',
  });

  const { data: selectedMacro } = useQuery({
    queryKey: ['insights', 'macro', selection?.id],
    queryFn: () => insightsApi.getMacroInsight(selection!.id),
    enabled: selection?.type === 'macro',
  });

  const { data: selectedUnit } = useQuery({
    queryKey: ['insights', 'unit', selection?.id],
    queryFn: () => insightsApi.getUnitInsight(selection!.id),
    enabled: selection?.type === 'unit',
  });

  const handleNodeClick = useCallback((type: SelectionType, id: number) => {
    console.log(`🎯 Node clicked: type=${type}, id=${id}`);
    setSelection({ type, id });
  }, []);

  const handleClosePanel = useCallback(() => {
    setSelection(null);
  }, []);

  const handleOpenVideo = useCallback((url: string, start: number, end?: number, title?: string, subtitle?: string) => {
    setVideoModal({ open: true, url, start, end, title, subtitle });
  }, []);

  const handleCloseVideo = useCallback(() => {
    setVideoModal({ open: false });
  }, []);

  const handleOpenPdf = useCallback((url: string, page: number, title: string) => {
    setPdfModal({ open: true, pdfUrl: url, initialPage: page, title });
  }, []);

  const handleClosePdf = useCallback(() => {
    setPdfModal({ open: false });
  }, []);

  // Log selection changes
  useEffect(() => {
    console.log('📍 Selection changed:', selection);
  }, [selection]);

  // Keyboard shortcut for closing modals
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (videoModal.open) handleCloseVideo();
        if (pdfModal.open) handleClosePdf();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [videoModal.open, pdfModal.open, handleCloseVideo, handleClosePdf]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <Header
        title="Health Trends Network Visualization"
        subtitle="Interactive exploration of emerging health innovations and adoption patterns"
      />

      {/* View Mode Toggle */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setViewMode('network')}
              className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors',
                viewMode === 'network'
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              <Network className="w-4 h-4" />
              Network View
            </button>
            <button
              onClick={() => setViewMode('trends')}
              className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors',
                viewMode === 'trends'
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              <BarChart3 className="w-4 h-4" />
              Trends View
            </button>
            <button
              onClick={() => setViewMode('stakes')}
              className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors',
                viewMode === 'stakes'
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              <AlertCircle className="w-4 h-4" />
              Stakes View
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 relative overflow-hidden">
        {viewMode === 'network' ? (
          <>
            {isLoading ? (
              <div className="flex items-center justify-center h-full">
                <LoadingState message="Loading insights..." />
              </div>
            ) : !hierarchy || (hierarchy.clusters.length === 0 && hierarchy.orphan_unit_insights.length === 0) ? (
              <div className="flex items-center justify-center h-full">
                <EmptyState
                  icon={Sparkles}
                  title="No insights yet"
                  description="Run the analysis pipeline to discover health trends and insights."
                />
              </div>
            ) : (
              <NetworkVisualization
                hierarchy={hierarchy}
                selection={selection}
                onNodeClick={handleNodeClick}
              />
            )}

            {/* Controls Hint */}
            <div className="network-controls">
              <strong>Controls:</strong>
              Scroll to zoom • Click & drag to pan • Click nodes for details
            </div>
          </>
        ) : (
          <div className="h-full overflow-hidden">
            {isLoadingViz ? (
              <div className="flex items-center justify-center h-full">
                <LoadingState message="Loading visualization data..." />
              </div>
            ) : !vizData ? (
              <div className="flex items-center justify-center h-full">
                <EmptyState
                  icon={BarChart3}
                  title="No visualization data"
                  description="Unable to load visualization data."
                />
              </div>
            ) : viewMode === 'trends' && vizData.trends.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <EmptyState
                  icon={TrendingUp}
                  title="No trend data"
                  description="No trends with dimension assessments found."
                />
              </div>
            ) : viewMode === 'stakes' && vizData.stakes.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <EmptyState
                  icon={AlertCircle}
                  title="No stake data"
                  description="No health stakes with dimension assessments found."
                />
              </div>
            ) : (
              <div className="w-full h-full flex flex-col p-6 gap-4">
                <div>
                  <h2 className="text-2xl font-bold">
                    {viewMode === 'trends' ? 'Trends Analysis' : 'Health Stakes Analysis'}
                  </h2>
                  <p className="text-gray-600 text-sm mt-1">
                    {viewMode === 'trends'
                      ? 'Visualizing trends by expectation level and progress horizon, colored by adoption stage.'
                      : 'Visualizing health stakes by criticality and urgency, colored by actionability level.'}
                  </p>
                </div>
                <div className="flex-1 min-h-0">
                  <ScatterPlotVisualization
                    data={viewMode === 'trends' ? vizData.trends : vizData.stakes}
                    type={viewMode === 'trends' ? 'trend' : 'stake'}
                    selectedId={selection?.type === 'unit' ? selection.id : null}
                    onPointClick={(id) => setSelection({ type: 'unit', id })}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Detail Panel */}
        <DetailPanel
          selection={selection}
          cluster={selectedCluster}
          macro={selectedMacro}
          unit={selectedUnit}
          onClose={handleClosePanel}
          onSelectMacro={(id) => setSelection({ type: 'macro', id })}
          onSelectUnit={(id) => setSelection({ type: 'unit', id })}
          onOpenVideo={handleOpenVideo}
          onOpenPdf={handleOpenPdf}
        />

        {/* Video Modal */}
        <VideoModal
          open={videoModal.open}
          url={videoModal.url}
          start={videoModal.start}
          end={videoModal.end}
          title={videoModal.title}
          subtitle={videoModal.subtitle}
          onClose={handleCloseVideo}
        />

        {/* PDF Modal */}
        <PdfModal
          open={pdfModal.open}
          pdfUrl={pdfModal.pdfUrl || null}
          initialPage={pdfModal.initialPage}
          title={pdfModal.title}
          onClose={handleClosePdf}
        />
      </div>
    </div>
  );
}

