import { UnitInsight, MacroInsight, Cluster, EvidenceItem } from '../types';
import { SourceBadge, AudioPlayer } from './common';
import { EvidenceCard } from './EvidenceCard';
import { PdfModal } from './PdfModal';
import {
  TrendingUp,
  AlertCircle,
  Clock,
  Layers,
  FileText,
  Play,
  X,
  ChevronRight,
  Sparkles,
} from 'lucide-react';
import clsx from 'clsx';
import { useState } from 'react';

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
  trend: '#8b5cf6',
  health_stake: '#f59e0b',
  cluster: '#0066B3',
  macro: '#2a894d',
};

const STAGE_COLORS: Record<string, string> = {
  'Nascent experimentation': '#c4b5fd',
  'Early adoption': '#93c5fd',
  'Crossing the chasm': '#41f1ce',
  'Established practice': '#fcd34d',
};

// ============================================
// Helper Functions
// ============================================

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function truncateToWords(text: string, wordLimit: number = 50): string {
  const words = text.trim().split(/\s+/);
  if (words.length <= wordLimit) {
    return text;
  }
  return words.slice(0, wordLimit).join(' ') + '…';
}

function parseSourceName(sourceRef: string | undefined): string | undefined {
  if (!sourceRef) return undefined;
  const parts = sourceRef.split(' | ');
  return parts[0]?.trim();
}

function selectRepresentativeEvidence(evidence: EvidenceItem[], count: number): EvidenceItem[] {
  if (evidence.length === 0) return [];
  if (evidence.length <= count) return evidence;

  // Filter evidence with timestamps (for podcasts)
  const evidenceWithTimestamps = evidence.filter((e) => e.start_time !== undefined);

  // If we have enough evidence with timestamps, select from those
  if (evidenceWithTimestamps.length >= count) {
    // Sort by timestamp
    const sorted = [...evidenceWithTimestamps].sort((a, b) => (a.start_time || 0) - (b.start_time || 0));

    // Select evenly distributed samples
    const indices = [];
    const step = sorted.length / count;
    for (let i = 0; i < count; i++) {
      indices.push(Math.floor(i * step));
    }

    return indices.map((i) => sorted[i]);
  }

  // For documents or mixed sources, just take the first N items
  return evidence.slice(0, count);
}

// ============================================
// Main DetailPanel Component
// ============================================

interface DetailPanelProps {
  selection: Selection | null;
  cluster?: Cluster;
  macro?: MacroInsight;
  unit?: UnitInsight;
  onClose: () => void;
  onSelectMacro: (id: number) => void;
  onSelectUnit: (id: number) => void;
  onOpenVideo: (url: string, start: number, end?: number, title?: string, subtitle?: string) => void;
  onOpenPdf: (url: string, page: number, title: string) => void;
}

export function DetailPanel({
  selection,
  cluster,
  macro,
  unit,
  onClose,
  onSelectMacro,
  onSelectUnit,
  onOpenVideo,
  onOpenPdf,
}: DetailPanelProps) {
  const isOpen = selection !== null;

  return (
    <div className={clsx('detail-panel', isOpen && 'open')}>
      {selection && (
        <>
          {/* Header */}
          <div className="panel-header" style={{ backgroundColor: '#0066B3', borderBottom: 'none' }}>
            <button
              className="panel-close-btn"
              onClick={onClose}
              style={{ backgroundColor: 'rgba(255, 255, 255, 0.1)' }}
            >
              <X className="w-5 h-5 text-white" />
            </button>

            <div className="panel-header-content">
              <div className="panel-section-label text-white text-opacity-90">
                {selection.type === 'cluster' && 'Strategic Cluster'}
                {selection.type === 'macro' && 'Macro Insight'}
                {selection.type === 'unit' && 'Unit Insight'}
              </div>

              <h2 className="text-xl font-semibold text-white mb-3 leading-tight">
                {selection.type === 'cluster' && cluster?.name}
                {selection.type === 'macro' && macro?.name}
                {selection.type === 'unit' && unit?.name}
              </h2>

              {/* Type Badge */}
              {selection.type === 'cluster' && (
                <span className="inline-flex items-center px-3 py-1 rounded-md text-sm font-medium bg-white bg-opacity-40 text-white">
                  <Layers className="w-4 h-4 mr-1.5" />
                  Cluster
                </span>
              )}
              {selection.type === 'macro' && (
                <span className="inline-flex items-center px-3 py-1 rounded-md text-sm font-medium bg-green-100 text-green-800">
                  Macro Insight
                </span>
              )}
              {selection.type === 'unit' && unit && (
                <span
                  className={clsx(
                    "inline-flex items-center px-3 py-1 rounded-md text-sm font-medium",
                    unit.type === 'trend'
                      ? "bg-purple-100 text-purple-800"
                      : "bg-amber-100 text-amber-800"
                  )}
                >
                  {unit.type === 'trend' ? (
                    <>
                      <TrendingUp className="w-4 h-4 mr-1.5" />
                      Trend
                    </>
                  ) : (
                    <>
                      <AlertCircle className="w-4 h-4 mr-1.5" />
                      Health Stake
                    </>
                  )}
                </span>
              )}
            </div>
          </div>

          {/* Content */}
          <div className="panel-content">
            {selection.type === 'cluster' && cluster && (
              <ClusterDetail cluster={cluster} onSelectMacro={onSelectMacro} onSelectUnit={onSelectUnit} />
            )}
            {selection.type === 'macro' && macro && (
              <MacroDetail macro={macro} onSelectUnit={onSelectUnit} />
            )}
            {selection.type === 'unit' && unit && (
              <UnitDetail unit={unit} onOpenVideo={onOpenVideo} onOpenPdf={onOpenPdf} />
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ============================================
// Cluster Detail Component
// ============================================

function ClusterDetail({
  cluster,
  onSelectMacro,
  onSelectUnit,
}: {
  cluster: Cluster;
  onSelectMacro: (id: number) => void;
  onSelectUnit: (id: number) => void;
}) {
  // Collect all unit insights from all macro insights
  const allUnitInsights: UnitInsight[] = cluster.macro_insights
    ?.flatMap((macro) => macro.unit_insights || [])
    .filter(Boolean) || [];

  // Get orphan unit insights (standalone insights directly assigned to cluster)
  const orphanUnitInsights: UnitInsight[] = cluster.orphan_unit_insights || [];

  // Total count includes both grouped and standalone unit insights
  const totalUnitInsightCount = allUnitInsights.length + orphanUnitInsights.length;

  return (
    <div className="detail-sections">
      <div className="panel-section">
        <h3 className="panel-section-title">Statistics</h3>
        <div className="info-card">
          <div className="info-card-label">Macro Insights</div>
          <div className="info-card-value">{cluster.macro_insight_count}</div>
        </div>
        <div className="info-card">
          <div className="info-card-label">Unit Insights</div>
          <div className="info-card-value">{totalUnitInsightCount}</div>
        </div>
        {orphanUnitInsights.length > 0 && (
          <div className="info-card">
            <div className="info-card-label">Standalone Insights</div>
            <div className="info-card-value">{orphanUnitInsights.length}</div>
          </div>
        )}
      </div>

      {cluster.macro_insights && cluster.macro_insights.length > 0 && (
        <div className="panel-section">
          <h3 className="panel-section-title">
            Macro Insights ({cluster.macro_insights.length})
          </h3>
          <div className="insight-list">
            {cluster.macro_insights.map((macro) => (
              <button
                key={macro.id}
                onClick={() => onSelectMacro(macro.id)}
                className="insight-list-item"
                style={{ borderLeftColor: TYPE_COLORS.macro }}
              >
                <div className="insight-list-header">
                  <span className="insight-list-name">{macro.name}</span>
                  <ChevronRight className="w-4 h-4 text-gray-400" />
                </div>
                <span className="insight-list-meta">
                  {macro.unit_insight_count} unit insights
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {allUnitInsights.length > 0 && (
        <div className="panel-section">
          <h3 className="panel-section-title">
            Grouped Unit Insights ({allUnitInsights.length})
          </h3>
          <div className="insight-list">
            {allUnitInsights.map((unit) => {
              const typeColor = TYPE_COLORS[unit.type] || '#64748b';
              const typeLabel = unit.type === 'health_stake' ? 'Health Stake' : 'Trend';

              return (
                <button
                  key={unit.id}
                  onClick={() => onSelectUnit(unit.id)}
                  className="insight-list-item"
                  style={{ borderLeftColor: typeColor }}
                >
                  <div className="insight-list-header">
                    {unit.type === 'trend' ? (
                      <TrendingUp className="w-4 h-4 text-purple-500 mr-2" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-amber-500 mr-2" />
                    )}
                    <span className="insight-list-name">{unit.name}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <SourceBadge sourceType={unit.source_type} size="sm" />
                    <span className="text-xs font-medium" style={{ color: typeColor }}>
                      {typeLabel}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {orphanUnitInsights.length > 0 && (
        <div className="panel-section">
          <h3 className="panel-section-title">
            Standalone Unit Insights ({orphanUnitInsights.length})
          </h3>
          <div className="insight-list">
            {orphanUnitInsights.map((unit) => {
              const typeColor = TYPE_COLORS[unit.type] || '#64748b';
              const typeLabel = unit.type === 'health_stake' ? 'Health Stake' : 'Trend';

              return (
                <button
                  key={unit.id}
                  onClick={() => onSelectUnit(unit.id)}
                  className="insight-list-item"
                  style={{ borderLeftColor: typeColor }}
                >
                  <div className="insight-list-header">
                    {unit.type === 'trend' ? (
                      <TrendingUp className="w-4 h-4 text-purple-500 mr-2" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-amber-500 mr-2" />
                    )}
                    <span className="insight-list-name">{unit.name}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <SourceBadge sourceType={unit.source_type} size="sm" />
                    <span className="text-xs font-medium" style={{ color: typeColor }}>
                      {typeLabel}
                    </span>
                    <span className="text-xs text-gray-500 italic">Standalone</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================
// Macro Detail Component
// ============================================

function MacroDetail({
  macro,
  onSelectUnit,
}: {
  macro: MacroInsight;
  onSelectUnit: (id: number) => void;
}) {
  return (
    <div className="detail-sections">
      {macro.description && (
        <div className="panel-section">
          <h3 className="panel-section-title">Description</h3>
          <p className="panel-section-content">{macro.description}</p>
        </div>
      )}

      <div className="panel-section">
        <h3 className="panel-section-title">Statistics</h3>
        <div className="info-card">
          <div className="info-card-label">Unit Insights</div>
          <div className="info-card-value">{macro.unit_insight_count}</div>
        </div>
      </div>

      {macro.unit_insights && macro.unit_insights.length > 0 && (
        <div className="panel-section">
          <h3 className="panel-section-title">
            Unit Insights ({macro.unit_insights.length})
          </h3>
          <div className="insight-list">
            {macro.unit_insights.map((unit) => {
              const typeColor = TYPE_COLORS[unit.type] || '#64748b';
              const typeLabel = unit.type === 'health_stake' ? 'Health Stake' : 'Trend';

              return (
                <button
                  key={unit.id}
                  onClick={() => onSelectUnit(unit.id)}
                  className="insight-list-item"
                  style={{ borderLeftColor: typeColor }}
                >
                  <div className="insight-list-header">
                    {unit.type === 'trend' ? (
                      <TrendingUp className="w-4 h-4 text-purple-500 mr-2" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-amber-500 mr-2" />
                    )}
                    <span className="insight-list-name">{unit.name}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <SourceBadge sourceType={unit.source_type} size="sm" />
                    <span className="text-xs font-medium" style={{ color: typeColor }}>
                      {typeLabel}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================
// Unit Detail Component
// ============================================

function UnitDetail({
  unit,
  onOpenVideo,
  onOpenPdf,
}: {
  unit: UnitInsight;
  onOpenVideo: (url: string, start: number, end?: number, title?: string, subtitle?: string) => void;
  onOpenPdf: (url: string, page: number, title: string) => void;
}) {
  // State for audio player modal - keep audio loaded once opened
  const [audioPlayerState, setAudioPlayerState] = useState<{
    open: boolean;
    url: string;
    startTime: number;
    title: string;
  } | null>(null);

  // State for PDF modal
  const [pdfModalState, setPdfModalState] = useState<{
    open: boolean;
    url: string;
    page: number;
    title: string;
  } | null>(null);

  // Handlers for media playback
  const handlePlayAudio = (url: string, startTime: number, title: string) => {
    if (audioPlayerState && audioPlayerState.url === url) {
      // Audio is already loaded, just seek to the new timestamp
      setAudioPlayerState({ ...audioPlayerState, startTime, open: true });
    } else {
      // Different audio source, need to reload
      setAudioPlayerState({ open: true, url, startTime, title });
    }
  };

  const handleOpenPdf = (url: string, page: number, title: string) => {
    setPdfModalState({ open: true, url, page, title });
  };

  const adoptionDim = unit.dimensions.find((d) => d.dimension_type === 'adoption');
  const expectationDim = unit.dimensions.find((d) => d.dimension_type === 'expectation');
  const progressDim = unit.dimensions.find((d) => d.dimension_type === 'progress');
  const criticalityDim = unit.dimensions.find((d) => d.dimension_type === 'criticality');
  const urgencyDim = unit.dimensions.find((d) => d.dimension_type === 'urgency');
  const actionabilityDim = unit.dimensions.find((d) => d.dimension_type === 'actionability');

  const isTrend = unit.type === 'trend';
  const isStake = unit.type === 'health_stake';

  // Collect all evidence from all dimensions and deduplicate by text
  const allEvidence: EvidenceItem[] = unit.dimensions.flatMap((dim) => dim.evidence);

  // Deduplicate evidence by text content
  const uniqueEvidence: EvidenceItem[] = [];
  const seenTexts = new Set<string>();

  for (const item of allEvidence) {
    const normalizedText = item.text.trim().toLowerCase();
    if (!seenTexts.has(normalizedText)) {
      seenTexts.add(normalizedText);
      uniqueEvidence.push(item);
    }
  }

  return (
    <div className="detail-sections">
      {/* Description */}
      {unit.description && (
        <div className="panel-section">
          <h3 className="panel-section-title">Description</h3>
          <p className="panel-section-content">{unit.description}</p>
        </div>
      )}

      {/* Trend Dimensions */}
      {isTrend && (adoptionDim || expectationDim || progressDim) && (
        <div className="panel-section">
          <h3 className="panel-section-title">Trend Dimensions</h3>
          <div className="dimension-grid">
            {adoptionDim && (
              <div className="dimension-card">
                <div className="dimension-card-label">Adoption Stage</div>
                <div className="dimension-card-value">
                  <TrendingUp className="w-5 h-5 inline mr-2 text-purple-500" />
                  {adoptionDim.value}
                </div>
              </div>
            )}

            {expectationDim && (
              <div className="dimension-card">
                <div className="dimension-card-label">Expectation Level</div>
                <div className="dimension-card-value">
                  <Sparkles className="w-5 h-5 inline mr-2 text-yellow-500" />
                  {expectationDim.value}
                </div>
              </div>
            )}

            {progressDim && (
              <div className="dimension-card">
                <div className="dimension-card-label">Progress Horizon</div>
                <div className="dimension-card-value">
                  <Clock className="w-5 h-5 inline mr-2 text-blue-500" />
                  {progressDim.value}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Stake Dimensions */}
      {isStake && (criticalityDim || urgencyDim || actionabilityDim) && (
        <div className="panel-section">
          <h3 className="panel-section-title">Stake Dimensions</h3>
          <div className="dimension-grid">
            {criticalityDim && (
              <div className="dimension-card">
                <div className="dimension-card-label">Criticality</div>
                <div className="dimension-card-value">
                  <AlertCircle className="w-5 h-5 inline mr-2 text-red-500" />
                  {criticalityDim.value}
                </div>
              </div>
            )}

            {urgencyDim && (
              <div className="dimension-card">
                <div className="dimension-card-label">Urgency</div>
                <div className="dimension-card-value">
                  <Clock className="w-5 h-5 inline mr-2 text-orange-500" />
                  {urgencyDim.value}
                </div>
              </div>
            )}

            {actionabilityDim && (
              <div className="dimension-card">
                <div className="dimension-card-label">Actionability</div>
                <div className="dimension-card-value">
                  <TrendingUp className="w-5 h-5 inline mr-2 text-green-500" />
                  {actionabilityDim.value}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Evidence - All unique chunks from dimension assessment */}
      {uniqueEvidence.length > 0 && (
        <div className="panel-section">
          <h3 className="panel-section-title">Evidence ({uniqueEvidence.length})</h3>

          <div className="evidence-list">
            {uniqueEvidence.map((item, i) => (
              <EvidenceCard
                key={i}
                evidence={item}
                sourceType={unit.source_type}
                sourceId={unit.source_id || null}
                onPlayAudio={handlePlayAudio}
                onOpenPdf={handleOpenPdf}
              />
            ))}
          </div>
        </div>
      )}

      {/* Audio Player Modal - Persists across timestamp clicks */}
      {audioPlayerState?.open && (
        <div className="audio-player-modal">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-gray-900">{audioPlayerState.title}</h4>
            <button
              onClick={() => setAudioPlayerState((prev) => prev ? { ...prev, open: false } : null)}
              className="text-gray-400 hover:text-gray-600"
              title="Close audio player"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <AudioPlayer
            key={audioPlayerState.url}
            src={audioPlayerState.url}
            title={audioPlayerState.title}
            initialTime={audioPlayerState.startTime}
          />
        </div>
      )}

      {/* PDF Modal */}
      {pdfModalState?.open && (
        <PdfModal
          open={pdfModalState.open}
          pdfUrl={pdfModalState.url}
          initialPage={pdfModalState.page}
          title={pdfModalState.title}
          onClose={() => setPdfModalState(null)}
        />
      )}
    </div>
  );
}