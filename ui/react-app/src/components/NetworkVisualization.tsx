import { useEffect, useRef } from 'react';
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
// Color Constants
// ============================================

const TYPE_COLORS = {
  trend: '#3b82f6',
  health_stake: '#f97316',
  cluster: '#d1e3f5',
  macro: '#22c55e',
};

// ============================================
// Component Props
// ============================================

interface NetworkVisualizationProps {
  hierarchy: InsightHierarchy;
  selection: { type: SelectionType; id: number } | null;
  onNodeClick: (type: SelectionType, id: number) => void;
}

// ============================================
// Main Component
// ============================================

export function NetworkVisualization({ hierarchy, selection, onNodeClick }: NetworkVisualizationProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return;

    const width = 1800;
    const height = 1100;

    // Clear previous content
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('width', '100%')
      .attr('height', '100%')
      .attr('viewBox', `0 0 ${width} ${height}`)
      .classed('network-svg', true);

    const g = svg.append('g');

    // Zoom behavior
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 3])
      .on('zoom', (event) => {
        g.attr('transform', event.transform);
      });

    svg.call(zoom);
    svg.call(zoom.transform, d3.zoomIdentity.translate(450, 275).scale(0.5));

    // Build nodes with hierarchy - NO LINKS
    const nodes: NetworkNode[] = [];
    const clusterNodes: NetworkNode[] = [];

    // Add clusters
    hierarchy.clusters.forEach((cluster) => {
      // Calculate total insights for radius sizing
      const totalInsights = cluster.macro_insights.reduce((sum, macro) =>
        sum + (macro.unit_insights?.length || 0), 0
      ) + (cluster.orphan_unit_insights?.length || 0);

      const clusterNode: NetworkNode = {
        id: `cluster-${cluster.id}`,
        type: 'cluster',
        data: cluster,
        radius: Math.max(600, 500 + totalInsights * 20),
        color: TYPE_COLORS.cluster,
      };
      nodes.push(clusterNode);
      clusterNodes.push(clusterNode);

      // Add macro insights within this cluster
      cluster.macro_insights.forEach((macro) => {
        // Calculate macro radius based on number of unit insights
        const unitCount = macro.unit_insights?.length || 0;
        const macroRadius = Math.max(200, 180 + unitCount * 25);

        const macroNode: NetworkNode = {
          id: `macro-${macro.id}`,
          type: 'macro',
          data: macro,
          radius: macroRadius,
          color: TYPE_COLORS.macro,
          clusterId: clusterNode.id,
        };
        nodes.push(macroNode);

        // Add unit insights nested within this macro
        if (macro.unit_insights) {
          macro.unit_insights.forEach((unit) => {
            const unitNode: NetworkNode = {
              id: `unit-${unit.id}`,
              type: 'unit',
              data: unit,
              radius: 36,
              color: TYPE_COLORS[unit.type] || '#94a3b8',
              clusterId: clusterNode.id,
              macroId: macroNode.id,
            };
            nodes.push(unitNode);
          });
        }
      });

      // Add orphan unit insights directly to cluster
      if (cluster.orphan_unit_insights) {
        cluster.orphan_unit_insights.forEach((unit) => {
          const unitNode: NetworkNode = {
            id: `unit-${unit.id}`,
            type: 'unit',
            data: unit,
            radius: 36,
            color: TYPE_COLORS[unit.type] || '#94a3b8',
            clusterId: clusterNode.id,
          };
          nodes.push(unitNode);
        });
      }
    });

    // Add unassigned macro insights
    hierarchy.unassigned_macro_insights.forEach((macro) => {
      // Calculate macro radius based on number of unit insights
      const unitCount = macro.unit_insights?.length || 0;
      // Base radius: 120px, add 12px per unit insight (min 120, max 240)
      const macroRadius = Math.min(240, Math.max(120, 120 + unitCount * 12));

      const macroNode: NetworkNode = {
        id: `macro-${macro.id}`,
        type: 'macro',
        data: macro,
        radius: macroRadius,
        color: TYPE_COLORS.macro,
      };
      nodes.push(macroNode);

      if (macro.unit_insights) {
        macro.unit_insights.forEach((unit) => {
          const unitNode: NetworkNode = {
            id: `unit-${unit.id}`,
            type: 'unit',
            data: unit,
            radius: 36,
            color: TYPE_COLORS[unit.type] || '#94a3b8',
            macroId: macroNode.id,
          };
          nodes.push(unitNode);
        });
      }
    });

    // Add orphan unit insights
    hierarchy.orphan_unit_insights.forEach((unit) => {
      nodes.push({
        id: `unit-${unit.id}`,
        type: 'unit',
        data: unit,
        radius: 36,
        color: TYPE_COLORS[unit.type] || '#94a3b8',
      });
    });

    // Custom collision force for clusters
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

    // Create force simulation without links
    const simulation = d3.forceSimulation<NetworkNode>(nodes)
      .force('charge', d3.forceManyBody<NetworkNode>()
        .strength((d: NetworkNode) => {
          if (d.type === 'cluster') return -800;
          if (d.type === 'macro') return -300;
          return -50;
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

    // Track tick count for damping
    let tickCount = 0;

    // Draw node groups
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
      });

    // Draw circles
    nodeGroups.append('circle')
      .attr('r', (d) => d.radius)
      .attr('fill', (d) => d.color)
      .attr('class', (d) => d.type === 'cluster' ? 'network-cluster-circle' : 'network-node-circle')
      .attr('stroke', (d) => {
        if (selection?.type === d.type && selection?.id === d.data.id) {
          return '#000';
        }
        return '#fff';
      })
      .attr('stroke-width', (d) => {
        if (selection?.type === d.type && selection?.id === d.data.id) {
          return 4;
        }
        return d.type === 'cluster' ? 3 : 2;
      });

    // Add labels for clusters and macros
    nodeGroups.each(function(d) {
      const group = d3.select(this);

      if (d.type === 'cluster') {
        const words = d.data.name.split(/\s+/);
        const maxWidth = d.radius * 1.2;
        const fontSize = 12;
        const fontWeight = 800;
        // Approximate text width: font-size * 0.6 per character (bold adjustment)
        const charWidth = fontSize * 0.7;
        const lines: string[] = [];

        // Check if entire title fits in one line
        const fullText = words.join(' ');
        const estimatedWidth = fullText.length * charWidth;

        if (estimatedWidth <= maxWidth) {
          // Short title: use 1 line
          lines.push(fullText);
        } else {
          // Long title: split into maximum 2 lines
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
        const maxWidth = d.radius * 0.8; // Keep text well inside the circle
        const fontSize = 16;
        const charWidth = fontSize * 0.65; // More accurate character width for bold text
        const lines: string[] = [];
        let currentLine: string[] = [];

        // Break text into lines that fit within the macro node
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
              // If single word is too long, still add it but it might overflow
              lines.push(word);
              currentLine = [];
            }
          }
        });

        if (currentLine.length > 0) {
          lines.push(currentLine.join(' '));
        }

        // Ensure minimum 2 lines for longer text (helps with readability)
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

    // Add tooltips
    nodeGroups.append('title')
      .text((d) => d.data.name);

    // Tick function with containment logic
    simulation.on('tick', () => {
      tickCount++;
      if (tickCount > 200) simulation.alphaTarget(0);

      // Apply velocity damping
      const maxVel = Math.max(0.5, 3 - tickCount * 0.01);
      nodes.forEach((d) => {
        d.vx = Math.max(-maxVel, Math.min(maxVel, d.vx || 0));
        d.vy = Math.max(-maxVel, Math.min(maxVel, d.vy || 0));
      });

      // Prevent cluster overlap
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

      // Keep nested nodes contained within their parents
      nodes.forEach((d) => {
        if (d.type === 'unit') {
          // Units must stay within their macro (if they have one)
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
          // Units without macros stay within their cluster
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
          // Macros stay within their cluster
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
  }, [hierarchy, selection, onNodeClick]);

  return (
    <div ref={containerRef} className="network-container w-full h-full">
      <svg ref={svgRef} />
    </div>
  );
}
