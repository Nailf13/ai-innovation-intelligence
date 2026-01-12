import { UnitInsight, MacroInsight, Cluster, EvidenceItem } from '../types';
import { SourceBadge } from './common';
import {
  TrendingUp,
  AlertCircle,
  Clock,
  Layers,
  FileText,
  Play,
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
// Helper Functions
// ============================================

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// Truncate text to approximately N words
function truncateToWords(text: string, wordLimit: number = 50): string {
  const words = text.trim().split(/\s+/);
  if (words.length <= wordLimit) {
    return text;
  }
  return words.slice(0, wordLimit).join(' ') + '…';
}

// Parse source_ref to extract source name (first part before |)
function parseSourceName(sourceRef: string | undefined): string | undefined {
  if (!sourceRef) return undefined;
  const parts = sourceRef.split(' | ');
  return parts[0]?.trim();
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
  onOpenPdf: (page: number, path?: string) => void;
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
          <div className="panel-header">
            <button className="panel-close-btn" onClick={onClose}>
              ×
            </button>

            {selection.type === 'cluster' && cluster && (
              <>
                <div className="panel-cluster-name">Strategic Cluster</div>
                <div className="panel-title">{cluster.name}</div>
                <span className="panel-type-badge" style={{ background: '#6366f1', color: '#fff' }}>
                  <Layers className="w-3 h-3 inline mr-1" />
                  Cluster
                </span>
              </>
            )}

            {selection.type === 'macro' && macro && (
              <>
                <div className="panel-cluster-name">Macro Insight</div>
                <div className="panel-title">{macro.name}</div>
                <span className="panel-type-badge" style={{ background: TYPE_COLORS.macro, color: '#fff' }}>
                  Macro Insight
                </span>
              </>
            )}

            {selection.type === 'unit' && unit && (
              <>
                <div className="panel-cluster-name">Unit Insight</div>
                <div className="panel-title">{unit.name}</div>
                <span
                  className="panel-type-badge"
                  style={{
                    background: TYPE_COLORS[unit.type] || '#64748b',
                    color: '#fff'
                  }}
                >
                  {unit.type === 'trend' ? (
                    <><TrendingUp className="w-3 h-3 inline mr-1" />Trend</>
                  ) : (
                    <><AlertCircle className="w-3 h-3 inline mr-1" />Health Stake</>
                  )}
                </span>
              </>
            )}
          </div>

          {/* Content */}
          <div className="panel-content">
            {selection.type === 'cluster' && cluster && (
              <ClusterDetail
                cluster={cluster}
                onSelectMacro={onSelectMacro}
              />
            )}
            {selection.type === 'macro' && macro && (
              <MacroDetail
                macro={macro}
                onSelectUnit={onSelectUnit}
              />
            )}
            {selection.type === 'unit' && unit && (
              <UnitDetail
                unit={unit}
                onOpenVideo={onOpenVideo}
                onOpenPdf={onOpenPdf}
              />
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
  onSelectMacro
}: {
  cluster: Cluster;
  onSelectMacro: (id: number) => void;
}) {
  return (
    <>
      {cluster.description && (
        <div className="panel-section">
          <div className="panel-section-title">Description</div>
          <div className="panel-section-content">{cluster.description}</div>
        </div>
      )}

      <div className="panel-section">
        <div className="panel-section-title">Statistics</div>
        <div className="info-grid">
          <div className="info-card">
            <div className="info-card-label">Macro Insights</div>
            <div className="info-card-value">{cluster.macro_insight_count}</div>
          </div>
        </div>
      </div>

      {cluster.macro_insights && cluster.macro_insights.length > 0 && (
        <div className="panel-section">
          <div className="panel-section-title">
            Macro Insights ({cluster.macro_insights.length})
          </div>
          <div className="panel-section-content">
            {cluster.macro_insights.map((macro) => (
              <div
                key={macro.id}
                className="insight-list-item"
                style={{ borderLeft: `4px solid ${TYPE_COLORS.macro}` }}
                onClick={() => onSelectMacro(macro.id)}
              >
                <div className="font-semibold text-gray-900 mb-1">
                  {macro.name}
                </div>
                <div className="text-xs text-gray-500">
                  {macro.unit_insight_count} unit insights
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

// ============================================
// Macro Detail Component
// ============================================

function MacroDetail({
  macro,
  onSelectUnit
}: {
  macro: MacroInsight;
  onSelectUnit: (id: number) => void;
}) {
  return (
    <>
      {macro.description && (
        <div className="panel-section">
          <div className="panel-section-title">Description</div>
          <div className="panel-section-content">{macro.description}</div>
        </div>
      )}

      <div className="panel-section">
        <div className="panel-section-title">Statistics</div>
        <div className="info-grid">
          <div className="info-card">
            <div className="info-card-label">Unit Insights</div>
            <div className="info-card-value">{macro.unit_insight_count}</div>
          </div>
        </div>
      </div>

      {macro.unit_insights && macro.unit_insights.length > 0 && (
        <div className="panel-section">
          <div className="panel-section-title">
            Unit Insights ({macro.unit_insights.length})
          </div>
          <div className="panel-section-content">
            {macro.unit_insights.map((unit) => {
              const typeColor = TYPE_COLORS[unit.type] || '#64748b';
              const typeLabel = unit.type === 'health_stake' ? 'Health Stake' : 'Trend';

              return (
                <div
                  key={unit.id}
                  className="insight-list-item"
                  style={{ borderLeft: `4px solid ${typeColor}` }}
                  onClick={() => onSelectUnit(unit.id)}
                >
                  <div className="font-semibold text-gray-900 mb-1">
                    {unit.name}
                  </div>
                  <div className="flex items-center gap-2">
                    <SourceBadge sourceType={unit.source_type} size="sm" />
                    <span className="text-xs font-semibold" style={{ color: typeColor }}>
                      {typeLabel}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
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
  onOpenPdf: (page: number, path?: string) => void;
}) {
  // Trend dimensions
  const adoptionDim = unit.dimensions.find((d) => d.dimension_type === 'adoption');
  const expectationDim = unit.dimensions.find((d) => d.dimension_type === 'expectation');
  const progressDim = unit.dimensions.find((d) => d.dimension_type === 'progress');

  // Stake dimensions
  const criticalityDim = unit.dimensions.find((d) => d.dimension_type === 'criticality');
  const urgencyDim = unit.dimensions.find((d) => d.dimension_type === 'urgency');
  const actionabilityDim = unit.dimensions.find((d) => d.dimension_type === 'actionability');

  const isTrend = unit.type === 'trend';
  const isStake = unit.type === 'health_stake';

  return (
    <>
      {/* Description */}
      {unit.description && (
        <div className="panel-section">
          <div className="panel-section-title">Description</div>
          <div className="panel-section-content">{unit.description}</div>
        </div>
      )}

      {/* Trend Dimensions */}
      {isTrend && (adoptionDim || expectationDim || progressDim) && (
        <div className="panel-section">
          <div className="panel-section-title">Trend Dimensions</div>
          <div className="info-grid">
            {adoptionDim && (
              <div className="info-card">
                <div className="info-card-label">Adoption Stage</div>
                <div className="info-card-value">
                  <span
                    className="stage-badge text-xs"
                    style={{
                      background: STAGE_COLORS[adoptionDim.value] || '#64748b',
                      color: adoptionDim.value === 'Established practice' ? '#000' : '#fff'
                    }}
                  >
                    {adoptionDim.value}
                  </span>
                </div>
              </div>
            )}

            {expectationDim && (
              <div className="info-card">
                <div className="info-card-label">Expectation Level</div>
                <div className="info-card-value">{expectationDim.value}</div>
              </div>
            )}

            {progressDim && (
              <div className="info-card">
                <div className="info-card-label">Progress Horizon</div>
                <div className="info-card-value">
                  <Clock className="w-4 h-4 inline mr-1 text-blue-500" />
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
          <div className="panel-section-title">Stake Dimensions</div>
          <div className="info-grid">
            {criticalityDim && (
              <div className="info-card">
                <div className="info-card-label">Criticality</div>
                <div className="info-card-value">
                  <AlertCircle className="w-4 h-4 inline mr-1 text-red-500" />
                  {criticalityDim.value}
                </div>
              </div>
            )}

            {urgencyDim && (
              <div className="info-card">
                <div className="info-card-label">Urgency</div>
                <div className="info-card-value">
                  <Clock className="w-4 h-4 inline mr-1 text-orange-500" />
                  {urgencyDim.value}
                </div>
              </div>
            )}

            {actionabilityDim && (
              <div className="info-card">
                <div className="info-card-label">Actionability</div>
                <div className="info-card-value">
                  <TrendingUp className="w-4 h-4 inline mr-1 text-green-500" />
                  {actionabilityDim.value}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Trend Evidence */}
      {isTrend && (
        <>
          {adoptionDim && adoptionDim.evidence.length > 0 && (
            <DimensionEvidence
              title="Adoption Evidence"
              evidence={adoptionDim.evidence}
              confidence={adoptionDim.confidence}
              sourceType={unit.source_type}
              onOpenVideo={onOpenVideo}
              onOpenPdf={onOpenPdf}
            />
          )}

          {expectationDim && expectationDim.evidence.length > 0 && (
            <DimensionEvidence
              title="Expectation Evidence"
              evidence={expectationDim.evidence}
              confidence={expectationDim.confidence}
              sourceType={unit.source_type}
              onOpenVideo={onOpenVideo}
              onOpenPdf={onOpenPdf}
            />
          )}

          {progressDim && progressDim.evidence.length > 0 && (
            <DimensionEvidence
              title="Progress Evidence"
              evidence={progressDim.evidence}
              confidence={progressDim.confidence}
              sourceType={unit.source_type}
              onOpenVideo={onOpenVideo}
              onOpenPdf={onOpenPdf}
            />
          )}
        </>
      )}

      {/* Stake Evidence */}
      {isStake && (
        <>
          {criticalityDim && criticalityDim.evidence.length > 0 && (
            <DimensionEvidence
              title="Criticality Evidence"
              evidence={criticalityDim.evidence}
              confidence={criticalityDim.confidence}
              sourceType={unit.source_type}
              onOpenVideo={onOpenVideo}
              onOpenPdf={onOpenPdf}
            />
          )}

          {urgencyDim && urgencyDim.evidence.length > 0 && (
            <DimensionEvidence
              title="Urgency Evidence"
              evidence={urgencyDim.evidence}
              confidence={urgencyDim.confidence}
              sourceType={unit.source_type}
              onOpenVideo={onOpenVideo}
              onOpenPdf={onOpenPdf}
            />
          )}

          {actionabilityDim && actionabilityDim.evidence.length > 0 && (
            <DimensionEvidence
              title="Actionability Evidence"
              evidence={actionabilityDim.evidence}
              confidence={actionabilityDim.confidence}
              sourceType={unit.source_type}
              onOpenVideo={onOpenVideo}
              onOpenPdf={onOpenPdf}
            />
          )}
        </>
      )}
    </>
  );
}

// ============================================
// Dimension Evidence Component
// ============================================

interface DimensionEvidenceProps {
  title: string;
  evidence: EvidenceItem[];
  confidence?: number;
  sourceType: 'podcast' | 'document';
  onOpenVideo: (url: string, start: number, end?: number, title?: string, subtitle?: string) => void;
  onOpenPdf: (page: number, path?: string) => void;
}

function DimensionEvidence({
  title,
  evidence,
  confidence,
  sourceType,
  onOpenVideo,
  onOpenPdf,
}: DimensionEvidenceProps) {
  return (
    <div className="panel-section">
      <div className="panel-section-title">
        {title}
        {confidence !== undefined && (
          <span className="ml-2 text-xs font-normal text-gray-500">
            ({Math.round(confidence * 100)}% confidence)
          </span>
        )}
      </div>
      <div className="panel-section-content">
        {evidence.slice(0, 5).map((item, i) => {
          const sourceName = parseSourceName(item.source_ref);
          const truncatedText = truncateToWords(item.text, 50);

          return (
            <div key={i} className="evidence-card">
              <div className="evidence-quote">"{truncatedText}"</div>
              <div className="evidence-attribution">
                <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
                  <div className="flex items-center gap-2">
                    {sourceType === 'podcast' ? (
                      <>
                        <Play className="w-3 h-3" />
                        <span>Podcast</span>
                      </>
                    ) : (
                      <>
                        <FileText className="w-3 h-3" />
                        <span>Document</span>
                      </>
                    )}
                    {sourceName && (
                      <span className="text-gray-700 font-medium">• {sourceName}</span>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    {/* Podcast metadata: timestamp */}
                    {sourceType === 'podcast' && item.start_time !== undefined && (
                      <span className="text-blue-600 font-mono">
                        <Clock className="w-3 h-3 inline mr-1" />
                        {formatTime(item.start_time)}
                        {item.end_time && ` - ${formatTime(item.end_time)}`}
                      </span>
                    )}

                    {/* Document metadata: page */}
                    {sourceType === 'document' && item.page !== undefined && (
                      <span className="text-purple-600 font-medium">
                        <FileText className="w-3 h-3 inline mr-1" />
                        Page {item.page}
                      </span>
                    )}

                    {/* Similarity score if available */}
                    {item.similarity_score !== undefined && (
                      <span className="text-green-600 font-mono text-xs">
                        {Math.round(item.similarity_score * 100)}%
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
