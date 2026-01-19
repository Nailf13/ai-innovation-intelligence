import { useEffect, useRef, useState } from 'react';
import { InsightHierarchy } from '../types';
import * as d3 from 'd3';

// ============================================
// Types
// ============================================

type SelectionType = 'cluster' | 'macro' | 'unit' | null;

interface NetworkNode {
  id: string;
  type: 'cluster' | 'macro' | 'unit';
  data: any;
  radius: number;
  color: string;
  clusterId?: string;
  macroId?: string;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
  vx?: number;
  vy?: number;
}

// ============================================
// Color Constants - Hierarchical per-cluster palette
// ============================================

const CLUSTER_COLOR_MAP: Record<string, { base: string; light: string; veryLight: string }> = {
  'Metabolic Health & Lifestyle-Driven Conditions': {
    base: '#3B82F6', // Strong blue
    light: 'rgba(59,130,246,0.30)',
    veryLight: 'rgba(59,130,246,0.10)',
  },
  'Physical Resilience & Performance': {
    base: '#10B981', // Emerald
    light: 'rgba(16,185,129,0.30)',
    veryLight: 'rgba(16,185,129,0.10)',
  },
  'Healthy Aging, Longevity & Vitality': {
    base: '#6366F1', // Indigo (replaces yellow!)
    light: 'rgba(99,102,241,0.30)',
    veryLight: 'rgba(99,102,241,0.10)',
  },
  'Mental, Emotional & Cognitive Well-Being': {
    base: '#8B5CF6', // Violet
    light: 'rgba(139,92,246,0.30)',
    veryLight: 'rgba(139,92,246,0.10)',
  },
  'Immunity & Gut Health': {
    base: '#EF4444', // Red
    light: 'rgba(239,68,68,0.30)',
    veryLight: 'rgba(239,68,68,0.10)',
  },
  "Women's Health & Hormonal Balance": {
    base: '#F97316', // Orange
    light: 'rgba(249,115,22,0.30)',
    veryLight: 'rgba(249,115,22,0.10)',
  },
  'Other': {
    base: '#64748B', // Slate
    light: 'rgba(100,116,139,0.30)',
    veryLight: 'rgba(100,116,139,0.10)',
  },
};

const DEFAULT_CLUSTER_COLORS = {
  base: '#8C8C8C',
  light: 'rgba(140,140,140,0.30)',
  veryLight: 'rgba(140,140,140,0.10)',
};


function getClusterColors(clusterName: string) {
  return CLUSTER_COLOR_MAP[clusterName] || DEFAULT_CLUSTER_COLORS;
}


interface NetworkVisualizationProps {
  hierarchy: InsightHierarchy;
  selection: { type: SelectionType; id: number } | null;
  onNodeClick: (type: SelectionType, id: number) => void;
  onRefresh?: () => void;
}

export function NetworkVisualization({ hierarchy, selection, onNodeClick }: NetworkVisualizationProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<{
    visible: boolean;
    x: number;
    y: number;
    data: NetworkNode | null;
  }>({ visible: false, x: 0, y: 0, data: null });

  // Track new insights from the latest pipeline run
  // The backend provides the authoritative list via hierarchy.latest_run_new_insight_ids
  const [newInsightIds, setNewInsightIds] = useState<Set<number>>(new Set());

  // Update new insight IDs when hierarchy changes
  // The backend tells us which insights are new from the latest pipeline run
  useEffect(() => {
    console.log('🔍 Hierarchy changed, updating new insights from backend...');

    if (!hierarchy) {
      console.log('❌ No hierarchy data');
      setNewInsightIds(new Set());
      return;
    }

    const latestRunIds = hierarchy.latest_run_new_insight_ids || [];
    console.log(`✨ Backend reports ${latestRunIds.length} new insights from latest run:`, latestRunIds);

    setNewInsightIds(new Set(latestRunIds));
  }, [hierarchy]);

  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return;

    console.log('🎬 D3 visualization render triggered');

    const width = 1800;
    const height = 1100;

    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('width', '100%')
      .attr('height', '100%')
      .attr('viewBox', `0 0 ${width} ${height}`)
      .classed('network-svg', true);

    const g = svg.append('g');

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 3])
      .on('zoom', (event) => {
        g.attr('transform', event.transform);
      });

    svg.call(zoom);
    svg.call(zoom.transform, d3.zoomIdentity.translate(600, 375).scale(0.15));

    const nodes: NetworkNode[] = [];
    const clusterNodes: NetworkNode[] = [];

    hierarchy.clusters.forEach((cluster) => {
      const totalInsights = cluster.macro_insights.reduce((sum, macro) =>
        sum + (macro.unit_insights?.length || 0), 0
      ) + (cluster.orphan_unit_insights?.length || 0);

      const clusterColors = getClusterColors(cluster.name);

      const clusterNode: NetworkNode = {
        id: `cluster-${cluster.id}`,
        type: 'cluster',
        data: cluster,
        radius: Math.max(450, 450 + totalInsights * 30),
        color: clusterColors.veryLight,
      };
      nodes.push(clusterNode);
      clusterNodes.push(clusterNode);

      cluster.macro_insights.forEach((macro) => {
        const unitCount = macro.unit_insights?.length || 0;
        const macroRadius = Math.max(200, 180 + unitCount * 25);

        const macroNode: NetworkNode = {
          id: `macro-${macro.id}`,
          type: 'macro',
          data: macro,
          radius: macroRadius,
          color: clusterColors.light,
          clusterId: clusterNode.id,
        };
        nodes.push(macroNode);

        if (macro.unit_insights) {
          macro.unit_insights.forEach((unit) => {
            const unitNode: NetworkNode = {
              id: `unit-${unit.id}`,
              type: 'unit',
              data: unit,
              radius: 36,
              color: clusterColors.base,
              clusterId: clusterNode.id,
              macroId: macroNode.id,
            };
            nodes.push(unitNode);
          });
        }
      });

      if (cluster.orphan_unit_insights) {
        cluster.orphan_unit_insights.forEach((unit) => {
          const unitNode: NetworkNode = {
            id: `unit-${unit.id}`,
            type: 'unit',
            data: unit,
            radius: 36,
            color: clusterColors.base,
            clusterId: clusterNode.id,
          };
          nodes.push(unitNode);
        });
      }
    });

    hierarchy.unassigned_macro_insights.forEach((macro) => {
      const unitCount = macro.unit_insights?.length || 0;
      const macroRadius = Math.min(240, Math.max(120, 120 + unitCount * 12));

      const macroNode: NetworkNode = {
        id: `macro-${macro.id}`,
        type: 'macro',
        data: macro,
        radius: macroRadius,
        color: DEFAULT_CLUSTER_COLORS.light,
      };
      nodes.push(macroNode);

      if (macro.unit_insights) {
        macro.unit_insights.forEach((unit) => {
          const unitNode: NetworkNode = {
            id: `unit-${unit.id}`,
            type: 'unit',
            data: unit,
            radius: 36,
            color: DEFAULT_CLUSTER_COLORS.base,
            macroId: macroNode.id,
          };
          nodes.push(unitNode);
        });
      }
    });

    hierarchy.orphan_unit_insights.forEach((unit) => {
      nodes.push({
        id: `unit-${unit.id}`,
        type: 'unit',
        data: unit,
        radius: 36,
        color: DEFAULT_CLUSTER_COLORS.base,
      });
    });

    function customClusterCollision() {
      const padding = -10;
      const strongForce = 1.0;

      return function(alpha: number) {
        for (let i = 0; i < clusterNodes.length; i++) {
          const clusterA = clusterNodes[i];
          for (let j = i + 1; j < clusterNodes.length; j++) {
            const clusterB = clusterNodes[j];
            const dx = (clusterB.x || 0) - (clusterA.x || 0);
            const dy = (clusterB.y || 0) - (clusterA.y || 0);
            const distance = Math.sqrt(dx * dx + dy * dy);
            const minDistance = clusterA.radius + clusterB.radius + padding;

            if (distance < minDistance) {
              const force = (minDistance - distance) / distance * alpha * strongForce;
              const fx = dx * force;
              const fy = dy * force;
              clusterB.vx = (clusterB.vx || 0) + fx;
              clusterB.vy = (clusterB.vy || 0) + fy;
              clusterA.vx = (clusterA.vx || 0) - fx;
              clusterA.vy = (clusterA.vy || 0) - fy;
            }
          }
        }
      };
    }

    // KEY FIX: No centering forces for units - they distribute like macros do in clusters
    const simulation = d3.forceSimulation<NetworkNode>(nodes)
      .force('charge', d3.forceManyBody<NetworkNode>()
        .strength((d: NetworkNode) => {
          if (d.type === 'cluster') return -800;
          if (d.type === 'macro') return -300;
          return -100; // Strong repulsion for units
        })
        .distanceMax(300)
      )
      .force('center', d3.forceCenter(900, 550).strength(0.08))
      .force('customCollision', customClusterCollision())
      .force('insightCollision', d3.forceCollide<NetworkNode>()
        .radius((d: NetworkNode) => {
          if (d.type === 'unit') return d.radius + 8;
          if (d.type === 'macro') return d.radius + 20;
          return 0;
        })
        .strength(0.8)
      )
      .force('clusterLink', d3.forceRadial(200, 900, 550)
        .strength((d: any) => d.type === 'cluster' ? 0.04 : 0)
      )
      .alphaDecay(0.03)
      .alphaMin(0.005)
      .velocityDecay(0.6);

    let tickCount = 0;

    const nodeGroups = g.append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(nodes)
      .enter()
      .append('g')
      .attr('class', 'network-node')
      .style('cursor', 'pointer')
      .on('click', (event, d) => {
        event.stopPropagation();
        onNodeClick(d.type, d.data.id);
      })
      .on('mouseenter', function(event, d) {
        if (d.type === 'cluster') return;

        // For unit nodes, scale only the circles with class 'network-node-circle'
        // This excludes the yellow halo and border ring
        if (d.type === 'unit') {
          d3.select(this).select('.network-node-circle')
            .transition()
            .duration(200)
            .attr('r', (d.radius - 3) * 1.15)
            .attr('stroke-width', 3);
        } else {
          // For macro nodes, scale the single circle
          d3.select(this).select('circle')
            .transition()
            .duration(200)
            .attr('r', d.radius * 1.15)
            .attr('stroke-width', 3);
        }

        const rect = svgRef.current?.getBoundingClientRect();
        if (rect) {
          setTooltip({
            visible: true,
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
            data: d,
          });
        }
      })
      .on('mousemove', function(event, d) {
        if (d.type === 'cluster') return;

        const rect = svgRef.current?.getBoundingClientRect();
        if (rect) {
          setTooltip(prev => ({
            ...prev,
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
          }));
        }
      })
      .on('mouseleave', function(event, d) {
        if (d.type === 'cluster') return;

        // For unit nodes, restore only the filled circle (network-node-circle)
        // This excludes the yellow halo and border ring
        if (d.type === 'unit') {
          d3.select(this).select('.network-node-circle')
            .transition()
            .duration(200)
            .attr('r', d.radius - 3)
            .attr('stroke-width', 0);
        } else {
          // For macro nodes, restore the single circle
          d3.select(this).select('circle')
            .transition()
            .duration(200)
            .attr('r', d.radius)
            .attr('stroke-width', 2);
        }

        setTooltip(prev => ({ ...prev, visible: false }));
      });

    // Add circles with layered approach for unit insights
    nodeGroups.each(function(d) {
      const group = d3.select(this);

      if (d.type === 'unit') {
        const isNew = newInsightIds.has(d.data.id);
        const isDocument = d.data.source_type === 'document';
        const isTrend = d.data.type === 'trend';

        // Layer 1: Yellow halo for new insights
        if (isNew) {
          group.append('circle')
            .attr('r', d.radius + 8)
            .attr('fill', 'none')
            .attr('stroke', '#EAB308') // Yellow-500
            .attr('stroke-width', 8)
            .attr('pointer-events', 'none')
            .style('opacity', 0.8);
        }

        // Layer 2: Border ring (solid for podcasts, dashed for documents)
        // Using darker, more visible color
        group.append('circle')
          .attr('r', d.radius)
          .attr('fill', 'none')
          .attr('stroke', '#1e293b') // Slate-800 for better visibility
          .attr('stroke-width', 3)
          .attr('stroke-dasharray', isDocument ? '5,3' : 'none')
          .attr('pointer-events', 'none');

        // Layer 3: Filled circle
        group.append('circle')
          .attr('r', d.radius - 3)
          .attr('fill', d.color)
          .attr('class', 'network-node-circle')
          .attr('stroke', () => {
            if (selection?.type === d.type && selection?.id === d.data.id) {
              return '#000';
            }
            return 'none';
          })
          .attr('stroke-width', () => {
            if (selection?.type === d.type && selection?.id === d.data.id) {
              return 3;
            }
            return 0;
          });

        // Layer 4: Inner icon (↗ for trends, ⚠ for health stakes)
        // Trends in cyan/teal, Health Stakes in orange/amber
        group.append('text')
          .attr('x', 0)
          .attr('y', 0)
          .attr('text-anchor', 'middle')
          .attr('dominant-baseline', 'middle')
          .attr('font-size', '28px')
          .attr('fill', isTrend ? '#891402' : '#f59e0b') // Cyan-500 for trends, Amber-500 for stakes
          .attr('font-weight', 'bold')
          .attr('pointer-events', 'none')
          .style('user-select', 'none')
          .style('text-shadow', '0 0 3px rgba(0,0,0,0.3)')
          .text(isTrend ? '📈' : '⚠️');
      } else {
        // For clusters and macros, use original styling
        group.append('circle')
          .attr('r', d.radius)
          .attr('fill', d.color)
          .attr('class', d.type === 'cluster' ? 'network-cluster-circle' : 'network-node-circle')
          .attr('stroke', () => {
            if (selection?.type === d.type && selection?.id === d.data.id) {
              return '#000';
            }
            return '#fff';
          })
          .attr('stroke-width', () => {
            if (selection?.type === d.type && selection?.id === d.data.id) {
              return 4;
            }
            return d.type === 'cluster' ? 3 : 2;
          });
      }
    });

    console.log(`🎨 Rendered visual encoding for ${newInsightIds.size} new insights with yellow halos`);

    nodeGroups.each(function(d) {
      const group = d3.select(this);

      if (d.type === 'cluster') {
        const words = d.data.name.split(/\s+/);
        const maxWidth = d.radius * 1.2;
        const fontSize = 12;
        const fontWeight = 800;
        const charWidth = fontSize * 0.7;
        const lines: string[] = [];

        const fullText = words.join(' ');
        const estimatedWidth = fullText.length * charWidth;

        if (estimatedWidth <= maxWidth) {
          lines.push(fullText);
        } else {
          const midPoint = Math.ceil(words.length / 2);
          lines.push(words.slice(0, midPoint).join(' '));
          lines.push(words.slice(midPoint).join(' '));
        }

        const lineHeight = 60;
        const startY = -(lines.length - 1) * lineHeight / 2;

        lines.forEach((line, i) => {
          group.append('text')
            .attr('class', 'network-node-label network-cluster-label')
            .attr('dy', startY + i * lineHeight)
            .attr('font-size', fontSize)
            .attr('font-weight', fontWeight)
            .text(line);
        });
      } else if (d.type === 'macro') {
        const words = d.data.name.split(/\s+/);
        const maxWidth = d.radius * 0.8;
        const fontSize = 16;
        const charWidth = fontSize * 0.65;
        const lines: string[] = [];
        let currentLine: string[] = [];

        words.forEach((word: string) => {
          currentLine.push(word);
          const testLine = currentLine.join(' ');
          const estimatedWidth = testLine.length * charWidth;

          if (estimatedWidth > maxWidth) {
            if (currentLine.length > 1) {
              currentLine.pop();
              lines.push(currentLine.join(' '));
              currentLine = [word];
            } else {
              lines.push(word);
              currentLine = [];
            }
          }
        });

        if (currentLine.length > 0) {
          lines.push(currentLine.join(' '));
        }

        if (lines.length === 1 && lines[0].length * charWidth > maxWidth * 0.6) {
          const singleLine = lines[0].split(/\s+/);
          if (singleLine.length > 1) {
            const mid = Math.ceil(singleLine.length / 2);
            lines[0] = singleLine.slice(0, mid).join(' ');
            lines.push(singleLine.slice(mid).join(' '));
          }
        }

        const lineHeight = 48;
        const startY = -(lines.length - 1) * lineHeight / 2;

        lines.forEach((line, i) => {
          group.append('text')
            .attr('class', 'network-node-label network-insight-label')
            .attr('dy', startY + i * lineHeight)
            .attr('font-size', fontSize)
            .attr('font-weight', 700)
            .attr('text-anchor', 'middle')
            .text(line);
        });
      }
    });

    nodeGroups.append('title')
      .text((d) => d.data.name);

    // Add Legend (positioned on the right side)
    const legendX = width - 220;
    const legendY = 30;
    const legendGroup = svg.append('g')
      .attr('class', 'network-legend')
      .attr('transform', `translate(${legendX}, ${legendY})`);

    // Legend background - matching scatter plot style
    const legendWidth = 170;
    const legendHeight = 270;
    legendGroup.append('rect')
      .attr('x', -15)
      .attr('y', -15)
      .attr('width', legendWidth)
      .attr('height', legendHeight)
      .attr('rx', 12)
      .attr('fill', '#f8fafc')
      .attr('stroke', '#e2e8f0')
      .attr('stroke-width', 1);

    // Section 1: Insight Type
    legendGroup.append('text')
      .attr('x', 0)
      .attr('y', 5)
      .attr('font-size', '13px')
      .attr('font-weight', '700')
      .attr('fill', '#1e293b')
      .attr('letter-spacing', '0.5px')
      .text('Insight Type');

    // Trend icon
    const trendItem = legendGroup.append('g')
      .attr('transform', `translate(0, 30)`);

    trendItem.append('text')
      .attr('x', 10)
      .attr('y', 0)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('font-size', '12px')
      .attr('fill', '#891402')
      .attr('font-weight', 'bold')
      .style('pointer-events', 'none')
      .text('📈');

    trendItem.append('text')
      .attr('x', 26)
      .attr('y', 4)
      .attr('font-size', '11px')
      .attr('font-weight', '500')
      .attr('fill', '#475569')
      .text('Trend');

    // Health Stake icon
    const stakeItem = legendGroup.append('g')
      .attr('transform', `translate(0, 55)`);

    stakeItem.append('text')
      .attr('x', 10)
      .attr('y', 0)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('font-size', '12px')
      .attr('fill', '#f59e0b')
      .attr('font-weight', 'bold')
      .style('pointer-events', 'none')
      .text('⚠️');

    stakeItem.append('text')
      .attr('x', 26)
      .attr('y', 4)
      .attr('font-size', '11px')
      .attr('font-weight', '500')
      .attr('fill', '#475569')
      .text('Health Stake');

    // Section 2: Source Type
    legendGroup.append('text')
      .attr('x', 0)
      .attr('y', 100)
      .attr('font-size', '13px')
      .attr('font-weight', '700')
      .attr('fill', '#1e293b')
      .attr('letter-spacing', '0.5px')
      .text('Source Type');

    // Podcast (solid border)
    const podcastItem = legendGroup.append('g')
      .attr('transform', `translate(0, 125)`);

    podcastItem.append('circle')
      .attr('cx', 10)
      .attr('cy', 0)
      .attr('r', 8)
      .attr('fill', '#94a3b8')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2);

    podcastItem.append('text')
      .attr('x', 26)
      .attr('y', 4)
      .attr('font-size', '11px')
      .attr('font-weight', '500')
      .attr('fill', '#475569')
      .text('Podcast');

    // Document (dashed border)
    const documentItem = legendGroup.append('g')
      .attr('transform', `translate(0, 150)`);

    documentItem.append('circle')
      .attr('cx', 10)
      .attr('cy', 0)
      .attr('r', 8)
      .attr('fill', '#94a3b8')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', '3,2');

    documentItem.append('text')
      .attr('x', 26)
      .attr('y', 4)
      .attr('font-size', '11px')
      .attr('font-weight', '500')
      .attr('fill', '#475569')
      .text('Document');

    // Section 3: Status
    legendGroup.append('text')
      .attr('x', 0)
      .attr('y', 195)
      .attr('font-size', '13px')
      .attr('font-weight', '700')
      .attr('fill', '#1e293b')
      .attr('letter-spacing', '0.5px')
      .text('Status');

    // New Insight (red halo)
    const newItem = legendGroup.append('g')
      .attr('transform', `translate(0, 220)`);

    newItem.append('circle')
      .attr('cx', 10)
      .attr('cy', 0)
      .attr('r', 12)
      .attr('fill', 'none')
      .attr('stroke', '#EAB308')
      .attr('stroke-width', 3)
      .attr('opacity', 0.8);

    newItem.append('circle')
      .attr('cx', 10)
      .attr('cy', 0)
      .attr('r', 8)
      .attr('fill', '#94a3b8')
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2);

    newItem.append('text')
      .attr('x', 26)
      .attr('y', 4)
      .attr('font-size', '11px')
      .attr('font-weight', '500')
      .attr('fill', '#475569')
      .text('New Insight');

    simulation.on('tick', () => {
      tickCount++;
      if (tickCount > 200) simulation.alphaTarget(0);

      const maxVel = Math.max(0.5, 3 - tickCount * 0.01);
      nodes.forEach((d) => {
        d.vx = Math.max(-maxVel, Math.min(maxVel, d.vx || 0));
        d.vy = Math.max(-maxVel, Math.min(maxVel, d.vy || 0));
      });

      for (let i = 0; i < clusterNodes.length; i++) {
        for (let j = i + 1; j < clusterNodes.length; j++) {
          const a = clusterNodes[i];
          const b = clusterNodes[j];
          const dx = (b.x || 0) - (a.x || 0);
          const dy = (b.y || 0) - (a.y || 0);
          const dist = Math.sqrt(dx * dx + dy * dy);
          const minDist = a.radius + b.radius - 10;

          if (dist < minDist && dist > 0) {
            const overlap = (minDist - dist) * 0.2;
            const angle = Math.atan2(dy, dx);
            b.x = (b.x || 0) + Math.cos(angle) * overlap;
            b.y = (b.y || 0) + Math.sin(angle) * overlap;
            a.x = (a.x || 0) - Math.cos(angle) * overlap;
            a.y = (a.y || 0) - Math.sin(angle) * overlap;
          }
        }
      }

      nodes.forEach((d) => {
        if (d.type === 'unit') {
          if (d.macroId) {
            const macro = nodes.find(n => n.id === d.macroId);
            if (macro && macro.x !== undefined && macro.y !== undefined) {
              const dx = (d.x || 0) - macro.x;
              const dy = (d.y || 0) - macro.y;
              const dist = Math.sqrt(dx * dx + dy * dy);
              const maxDist = macro.radius - d.radius - 10;

              if (dist > maxDist) {
                const angle = Math.atan2(dy, dx);
                d.x = macro.x + Math.cos(angle) * maxDist;
                d.y = macro.y + Math.sin(angle) * maxDist;
              }
            }
          }
          else if (d.clusterId) {
            const cluster = nodes.find(n => n.id === d.clusterId);
            if (cluster && cluster.x !== undefined && cluster.y !== undefined) {
              const dx = (d.x || 0) - cluster.x;
              const dy = (d.y || 0) - cluster.y;
              const dist = Math.sqrt(dx * dx + dy * dy);
              const maxDist = cluster.radius - d.radius - 10;

              if (dist > maxDist) {
                const angle = Math.atan2(dy, dx);
                d.x = cluster.x + Math.cos(angle) * maxDist;
                d.y = cluster.y + Math.sin(angle) * maxDist;
              }
            }
          }
        } else if (d.type === 'macro') {
          if (d.clusterId) {
            const cluster = nodes.find(n => n.id === d.clusterId);
            if (cluster && cluster.x !== undefined && cluster.y !== undefined) {
              const dx = (d.x || 0) - cluster.x;
              const dy = (d.y || 0) - cluster.y;
              const dist = Math.sqrt(dx * dx + dy * dy);
              const maxDist = cluster.radius - d.radius - 10;

              if (dist > maxDist) {
                const angle = Math.atan2(dy, dx);
                d.x = cluster.x + Math.cos(angle) * maxDist;
                d.y = cluster.y + Math.sin(angle) * maxDist;
              }
            }
          }
        }
      });

      nodeGroups.attr('transform', (d) => `translate(${d.x || 0},${d.y || 0})`);
    });

    return () => {
      simulation.stop();
    };
  }, [hierarchy, onNodeClick, newInsightIds]);

  useEffect(() => {
    if (!svgRef.current) return;

    console.log('🎯 Updating selection highlighting:', selection);

    const svg = d3.select(svgRef.current);

    // Update selection highlighting on the filled circles (for unit nodes, this is the 3rd circle)
    svg.selectAll('.network-node').each(function() {
      const group = d3.select(this);
      const nodeData: any = group.datum();

      if (!nodeData) return;

      const isSelected = selection?.type === nodeData.type && selection?.id === nodeData.data.id;

      if (nodeData.type === 'unit') {
        // For unit nodes, find the filled circle (3rd circle if new, 2nd if not)
        const circles = group.selectAll('circle');
        const filledCircle = circles.filter(function(d, i) {
          const circle = d3.select(this);
          return circle.attr('class') === 'network-node-circle';
        });

        filledCircle
          .attr('stroke', isSelected ? '#000' : 'none')
          .attr('stroke-width', isSelected ? 3 : 0);
      } else {
        // For clusters and macros, use the single circle
        group.select('circle')
          .attr('stroke', isSelected ? '#000' : '#fff')
          .attr('stroke-width', isSelected ? 4 : (nodeData.type === 'cluster' ? 3 : 2));
      }
    });
  }, [selection, newInsightIds]);

  return (
    <div ref={containerRef} className="network-container w-full h-full relative">
      <svg ref={svgRef} />

      {tooltip.visible && tooltip.data && (
        <div
          className="absolute pointer-events-none z-50 animate-in fade-in-0 zoom-in-95 duration-200"
          style={{
            left: tooltip.x + 15,
            top: tooltip.y - 10,
            transform: tooltip.x > 900 ? 'translateX(-110%)' : 'none',
          }}
        >
          <div className="bg-white/95 backdrop-blur-sm rounded-xl shadow-2xl border border-slate-200/80 p-4 max-w-[320px]">
            <div className="mb-2">
              <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium ${
                tooltip.data.type === 'cluster'
                  ? 'bg-blue-100 text-blue-800'
                  : tooltip.data.type === 'macro'
                  ? 'bg-green-100 text-green-800'
                  : tooltip.data.data.type === 'trend'
                  ? 'bg-purple-100 text-purple-800'
                  : 'bg-amber-100 text-amber-800'
              }`}>
                {tooltip.data.type === 'cluster'
                  ? 'Strategic Cluster'
                  : tooltip.data.type === 'macro'
                  ? 'Macro Insight'
                  : tooltip.data.data.type === 'trend'
                  ? 'Trend'
                  : 'Health Stake'}
              </span>
            </div>

            <div className="font-bold text-slate-900 text-sm mb-2 leading-tight">
              {tooltip.data.data.name}
            </div>

            {tooltip.data.data.description && (
              <div className="text-xs text-slate-500 mb-3 leading-relaxed">
                {tooltip.data.data.description}
              </div>
            )}

            <div className="space-y-1.5 pt-2 border-t border-slate-100">
              {tooltip.data.type === 'cluster' && (
                <>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Macro Insights</span>
                    <span className="font-medium text-slate-700">
                      {tooltip.data.data.macro_insights?.length || 0}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Total Unit Insights</span>
                    <span className="font-medium text-slate-700">
                      {(tooltip.data.data.macro_insights?.reduce((sum: number, m: any) =>
                        sum + (m.unit_insights?.length || 0), 0) || 0) +
                       (tooltip.data.data.orphan_unit_insights?.length || 0)}
                    </span>
                  </div>
                </>
              )}

              {tooltip.data.type === 'macro' && (
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500">Unit Insights</span>
                  <span className="font-medium text-slate-700">
                    {tooltip.data.data.unit_insights?.length || 0}
                  </span>
                </div>
              )}

              {tooltip.data.type === 'unit' && (
                <>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Type</span>
                    <span className="font-medium text-slate-700">
                      {tooltip.data.data.type === 'trend' ? 'Trend' : 'Health Stake'}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Source</span>
                    <span className="font-medium text-slate-700 capitalize">
                      {tooltip.data.data.source_type === 'podcast' ? '🎙️ Podcast' :
                       tooltip.data.data.source_type === 'document' ? '📄 Document' : 'Unknown'}
                    </span>
                  </div>
                  {tooltip.data.macroId && (
                    <div className="text-xs text-slate-500 italic mt-1">
                      Grouped in macro insight
                    </div>
                  )}
                  {tooltip.data.clusterId && !tooltip.data.macroId && (
                    <div className="text-xs text-slate-500 italic mt-1">
                      Standalone in cluster
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="mt-3 pt-2 border-t border-slate-100 text-xs text-slate-400 italic">
              Click to view details →
            </div>
          </div>
        </div>
      )}
    </div>
  );
}