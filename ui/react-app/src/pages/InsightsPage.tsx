import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Header } from '../components/layout';
import {
  LoadingState,
  EmptyState,
  SourceBadge,
  AdoptionBadge,
  AdoptionLegend,
  AudioPlayer,
} from '../components/common';
import { insightsApi } from '../api';
import { UnitInsight, MacroInsight, Cluster, InsightHierarchy, Dimension } from '../types';
import {
  Sparkles,
  X,
  ChevronRight,
  TrendingUp,
  AlertCircle,
  Mic,
  FileText,
  Clock,
  ExternalLink,
  Layers,
  Circle,
} from 'lucide-react';
import * as d3 from 'd3';
import clsx from 'clsx';

type SelectionType = 'cluster' | 'macro' | 'unit' | null;

interface Selection {
  type: SelectionType;
  id: number;
}

export function InsightsPage() {
  const [selection, setSelection] = useState<Selection | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Fetch hierarchy
  const { data: hierarchy, isLoading } = useQuery({
    queryKey: ['insights', 'hierarchy'],
    queryFn: insightsApi.getHierarchy,
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
    setSelection({ type, id });
  }, []);

  return (
    <div className="h-full flex flex-col">
      <Header
        title="Health Trends Insights"
        subtitle="Explore discovered trends and health stakes"
      />

      <div className="flex-1 flex overflow-hidden">
        {/* Left Panel - Visualization */}
        <div className="flex-1 p-6 overflow-auto">
          {/* Legend */}
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full bg-savencia-primary" />
                <span className="text-sm text-gray-600">Cluster</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-green-500" />
                <span className="text-sm text-gray-600">Macro Insight</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-purple-500" />
                <span className="text-sm text-gray-600">Trend</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-amber-500" />
                <span className="text-sm text-gray-600">Health Stake</span>
              </div>
            </div>
            <AdoptionLegend />
          </div>

          {isLoading ? (
            <LoadingState message="Loading insights..." />
          ) : !hierarchy || (hierarchy.clusters.length === 0 && hierarchy.orphan_unit_insights.length === 0) ? (
            <EmptyState
              icon={Sparkles}
              title="No insights yet"
              description="Run the analysis pipeline to discover health trends and insights."
            />
          ) : (
            <ClusterVisualization
              hierarchy={hierarchy}
              selection={selection}
              onNodeClick={handleNodeClick}
            />
          )}
        </div>

        {/* Right Panel - Details */}
        {selection && (
          <DetailPanel
            selection={selection}
            cluster={selectedCluster}
            macro={selectedMacro}
            unit={selectedUnit}
            onClose={() => setSelection(null)}
            onSelectMacro={(id) => setSelection({ type: 'macro', id })}
            onSelectUnit={(id) => setSelection({ type: 'unit', id })}
          />
        )}
      </div>
    </div>
  );
}

interface ClusterVisualizationProps {
  hierarchy: InsightHierarchy;
  selection: Selection | null;
  onNodeClick: (type: SelectionType, id: number) => void;
}

function ClusterVisualization({ hierarchy, selection, onNodeClick }: ClusterVisualizationProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return;

    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;

    // Clear previous content
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height);

    // Create nodes data
    const nodes: any[] = [];
    const links: any[] = [];

    // Add clusters
    hierarchy.clusters.forEach((cluster) => {
      nodes.push({
        id: `cluster-${cluster.id}`,
        type: 'cluster',
        data: cluster,
        radius: 30,
        color: '#0066B3',
      });

      // Add macro insights
      cluster.macro_insights.forEach((macro) => {
        nodes.push({
          id: `macro-${macro.id}`,
          type: 'macro',
          data: macro,
          radius: 18,
          color: '#22c55e',
        });
        links.push({
          source: `cluster-${cluster.id}`,
          target: `macro-${macro.id}`,
        });

        // Add unit insights
        macro.unit_insights.forEach((unit) => {
          nodes.push({
            id: `unit-${unit.id}`,
            type: 'unit',
            data: unit,
            radius: 8,
            color: unit.type === 'trend' ? '#8b5cf6' : '#f59e0b',
          });
          links.push({
            source: `macro-${macro.id}`,
            target: `unit-${unit.id}`,
          });
        });
      });
    });

    // Add unassigned macro insights
    hierarchy.unassigned_macro_insights.forEach((macro) => {
      nodes.push({
        id: `macro-${macro.id}`,
        type: 'macro',
        data: macro,
        radius: 18,
        color: '#22c55e',
      });

      macro.unit_insights.forEach((unit) => {
        nodes.push({
          id: `unit-${unit.id}`,
          type: 'unit',
          data: unit,
          radius: 8,
          color: unit.type === 'trend' ? '#8b5cf6' : '#f59e0b',
        });
        links.push({
          source: `macro-${macro.id}`,
          target: `unit-${unit.id}`,
        });
      });
    });

    // Add orphan unit insights
    hierarchy.orphan_unit_insights.forEach((unit) => {
      nodes.push({
        id: `unit-${unit.id}`,
        type: 'unit',
        data: unit,
        radius: 8,
        color: unit.type === 'trend' ? '#8b5cf6' : '#f59e0b',
      });
    });

    // Create force simulation
    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d: any) => d.id).distance(60))
      .force('charge', d3.forceManyBody().strength(-150))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d: any) => d.radius + 5));

    // Draw links
    const link = svg.append('g')
      .selectAll('line')
      .data(links)
      .enter()
      .append('line')
      .attr('stroke', '#e5e7eb')
      .attr('stroke-width', 1);

    // Draw nodes
    const node = svg.append('g')
      .selectAll('circle')
      .data(nodes)
      .enter()
      .append('circle')
      .attr('r', (d: any) => d.radius)
      .attr('fill', (d: any) => d.color)
      .attr('stroke', (d: any) => {
        const isSelected = selection &&
          selection.type === d.type &&
          selection.id === d.data.id;
        return isSelected ? '#000' : 'transparent';
      })
      .attr('stroke-width', 3)
      .style('cursor', 'pointer')
      .on('click', (event: any, d: any) => {
        onNodeClick(d.type, d.data.id);
      });

    // Add tooltips
    node.append('title')
      .text((d: any) => d.data.name);

    // Add labels for clusters
    const labels = svg.append('g')
      .selectAll('text')
      .data(nodes.filter((n) => n.type === 'cluster'))
      .enter()
      .append('text')
      .text((d: any) => d.data.name)
      .attr('font-size', 10)
      .attr('fill', '#374151')
      .attr('text-anchor', 'middle')
      .attr('dy', (d: any) => d.radius + 14);

    // Update positions
    simulation.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);

      node
        .attr('cx', (d: any) => d.x)
        .attr('cy', (d: any) => d.y);

      labels
        .attr('x', (d: any) => d.x)
        .attr('y', (d: any) => d.y);
    });

    // Drag behavior
    node.call(
      d3.drag<SVGCircleElement, any>()
        .on('start', (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on('drag', (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on('end', (event, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
    );

    return () => {
      simulation.stop();
    };
  }, [hierarchy, selection, onNodeClick]);

  return (
    <div ref={containerRef} className="w-full h-[600px] bg-white rounded-xl border border-gray-200">
      <svg ref={svgRef} className="w-full h-full" />
    </div>
  );
}

interface DetailPanelProps {
  selection: Selection;
  cluster?: Cluster;
  macro?: MacroInsight;
  unit?: UnitInsight;
  onClose: () => void;
  onSelectMacro: (id: number) => void;
  onSelectUnit: (id: number) => void;
}

function DetailPanel({
  selection,
  cluster,
  macro,
  unit,
  onClose,
  onSelectMacro,
  onSelectUnit,
}: DetailPanelProps) {
  return (
    <div className="w-[450px] bg-white border-l border-gray-200 flex flex-col slide-in">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b">
        <h2 className="font-semibold text-gray-900">
          {selection.type === 'cluster' && 'Strategic Cluster'}
          {selection.type === 'macro' && 'Macro Insight'}
          {selection.type === 'unit' && 'Unit Insight'}
        </h2>
        <button
          onClick={onClose}
          className="p-2 hover:bg-gray-100 rounded-lg"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
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
          <UnitDetail unit={unit} />
        )}
      </div>
    </div>
  );
}

function ClusterDetail({ cluster, onSelectMacro }: { cluster: Cluster; onSelectMacro: (id: number) => void }) {
  return (
    <div>
      <h3 className="text-xl font-semibold text-gray-900 mb-2">{cluster.name}</h3>
      {cluster.description && (
        <p className="text-gray-600 mb-4">{cluster.description}</p>
      )}

      <div className="mb-4">
        <span className="text-sm text-gray-500">
          {cluster.macro_insight_count} macro insights
        </span>
      </div>

      <h4 className="font-medium text-gray-900 mb-3">Macro Insights</h4>
      <div className="space-y-2">
        {cluster.macro_insights?.map((macro) => (
          <button
            key={macro.id}
            onClick={() => onSelectMacro(macro.id)}
            className="w-full text-left p-3 bg-gray-50 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-gray-900">{macro.name}</span>
              <ChevronRight className="w-4 h-4 text-gray-400" />
            </div>
            <span className="text-sm text-gray-500">
              {macro.unit_insight_count} insights
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function MacroDetail({ macro, onSelectUnit }: { macro: MacroInsight; onSelectUnit: (id: number) => void }) {
  return (
    <div>
      <h3 className="text-xl font-semibold text-gray-900 mb-2">{macro.name}</h3>
      {macro.description && (
        <p className="text-gray-600 mb-4">{macro.description}</p>
      )}

      <div className="mb-4">
        <span className="text-sm text-gray-500">
          {macro.unit_insight_count} unit insights
        </span>
      </div>

      <h4 className="font-medium text-gray-900 mb-3">Unit Insights</h4>
      <div className="space-y-2">
        {macro.unit_insights?.map((unit) => (
          <button
            key={unit.id}
            onClick={() => onSelectUnit(unit.id)}
            className="w-full text-left p-3 bg-gray-50 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <div className="flex items-center gap-2 mb-1">
              {unit.type === 'trend' ? (
                <TrendingUp className="w-4 h-4 text-purple-500" />
              ) : (
                <AlertCircle className="w-4 h-4 text-amber-500" />
              )}
              <span className="font-medium text-gray-900">{unit.name}</span>
            </div>
            <div className="flex items-center gap-2">
              <SourceBadge sourceType={unit.source_type} size="sm" />
              <span className="text-xs text-gray-500 capitalize">{unit.type}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function UnitDetail({ unit }: { unit: UnitInsight }) {
  const adoptionDim = unit.dimensions.find((d) => d.dimension_type === 'adoption');
  const expectationDim = unit.dimensions.find((d) => d.dimension_type === 'expectation');
  const progressDim = unit.dimensions.find((d) => d.dimension_type === 'progress');

  return (
    <div>
      {/* Type badge */}
      <div className="flex items-center gap-2 mb-3">
        {unit.type === 'trend' ? (
          <span className="badge badge-info">
            <TrendingUp className="w-3 h-3 mr-1" />
            Trend
          </span>
        ) : (
          <span className="badge badge-warning">
            <AlertCircle className="w-3 h-3 mr-1" />
            Health Stake
          </span>
        )}
        <SourceBadge sourceType={unit.source_type} />
      </div>

      <h3 className="text-xl font-semibold text-gray-900 mb-2">{unit.name}</h3>
      <p className="text-gray-600 mb-6">{unit.description}</p>

      {/* Dimensions */}
      {unit.dimensions.length > 0 && (
        <div className="space-y-6">
          {adoptionDim && (
            <DimensionCard
              title="Adoption Level"
              dimension={adoptionDim}
              badge={<AdoptionBadge level={adoptionDim.value} />}
            />
          )}

          {expectationDim && (
            <DimensionCard
              title="Market Expectation"
              dimension={expectationDim}
              badge={
                <span className={clsx(
                  'badge',
                  expectationDim.value === 'High' && 'badge-success',
                  expectationDim.value === 'Moderate' && 'badge-warning',
                  expectationDim.value === 'Low' && 'badge-danger'
                )}>
                  {expectationDim.value}
                </span>
              }
            />
          )}

          {progressDim && (
            <DimensionCard
              title="Progress Horizon"
              dimension={progressDim}
              badge={
                <span className="badge badge-info">
                  <Clock className="w-3 h-3 mr-1" />
                  {progressDim.value}
                </span>
              }
            />
          )}
        </div>
      )}
    </div>
  );
}

function DimensionCard({
  title,
  dimension,
  badge,
}: {
  title: string;
  dimension: Dimension;
  badge: React.ReactNode;
}) {
  return (
    <div className="bg-gray-50 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="font-medium text-gray-900">{title}</h4>
        {badge}
      </div>

      {dimension.confidence !== undefined && (
        <div className="mb-3">
          <div className="flex items-center justify-between text-sm mb-1">
            <span className="text-gray-500">Confidence</span>
            <span className="font-medium">{Math.round(dimension.confidence * 100)}%</span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-savencia-primary rounded-full"
              style={{ width: `${dimension.confidence * 100}%` }}
            />
          </div>
        </div>
      )}

      {dimension.evidence.length > 0 && (
        <div>
          <h5 className="text-sm font-medium text-gray-700 mb-2">Supporting Evidence</h5>
          <div className="space-y-2">
            {dimension.evidence.slice(0, 3).map((text, i) => (
              <div
                key={i}
                className="p-3 bg-white rounded border border-gray-200 text-sm text-gray-600"
              >
                <p className="line-clamp-3">"{text}"</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
