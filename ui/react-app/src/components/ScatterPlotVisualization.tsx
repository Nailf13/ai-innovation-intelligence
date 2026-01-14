import { useRef, useEffect, useState } from 'react';
import * as d3 from 'd3';

// Types
export interface TrendVisualizationPoint {
  id: number;
  name: string;
  description: string;
  expectation?: string | null;
  progress?: string | null;
  adoption?: string | null;
}

export interface StakeVisualizationPoint {
  id: number;
  name: string;
  description: string;
  criticality?: string | null;
  urgency?: string | null;
  actionability?: string | null;
}

interface ScatterPlotProps {
  data: TrendVisualizationPoint[] | StakeVisualizationPoint[];
  type: 'trend' | 'stake';
  width?: number;
  height?: number;
  onPointClick?: (id: number) => void;
}

// Color schemes with beautiful gradients
const ADOPTION_COLORS: Record<string, { bg: string; dot: string; glow: string }> = {
  'Nascent experimentation': { bg: '#f3e8ff', dot: '#a855f7', glow: 'rgba(168, 85, 247, 0.4)' },
  'Early adoption': { bg: '#dbeafe', dot: '#3b82f6', glow: 'rgba(59, 130, 246, 0.4)' },
  'Crossing the chasm': { bg: '#d1fae5', dot: '#10b981', glow: 'rgba(16, 185, 129, 0.4)' },
  'Established practice': { bg: '#fef3c7', dot: '#f59e0b', glow: 'rgba(245, 158, 11, 0.4)' },
};

const ACTIONABILITY_COLORS: Record<string, { bg: string; dot: string; glow: string }> = {
  'Hard to address': { bg: '#fee2e2', dot: '#ef4444', glow: 'rgba(239, 68, 68, 0.4)' },
  'Moderately addressable': { bg: '#ffedd5', dot: '#f97316', glow: 'rgba(249, 115, 22, 0.4)' },
  'Highly addressable': { bg: '#dcfce7', dot: '#22c55e', glow: 'rgba(34, 197, 94, 0.4)' },
};

// Axis mappings
const EXPECTATION_LEVELS = ['Low', 'Moderate', 'High'];
const PROGRESS_LEVELS = ['Near-term (0-12 months)', 'Mid-term (1-3 years)', 'Long-term (3+ years)'];
const CRITICALITY_LEVELS = ['Low', 'Moderate', 'High'];
const URGENCY_LEVELS = ['Long-term', 'Mid-term', 'Immediate'];

// Cell background colors for matrix effect
const CELL_GRADIENTS = {
  trend: [
    ['#fefce8', '#fef9c3', '#fef08a'],
    ['#ecfdf5', '#d1fae5', '#a7f3d0'],
    ['#eff6ff', '#dbeafe', '#bfdbfe'],
  ],
  stake: [
    ['#fef2f2', '#fee2e2', '#fecaca'],
    ['#fffbeb', '#fef3c7', '#fde68a'],
    ['#f0fdf4', '#dcfce7', '#bbf7d0'],
  ],
};

export function ScatterPlotVisualization({ 
  data, 
  type, 
  width = 900, 
  height = 650, 
  onPointClick 
}: ScatterPlotProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<{
    visible: boolean;
    x: number;
    y: number;
    data: any;
  }>({ visible: false, x: 0, y: 0, data: null });

  useEffect(() => {
    if (!svgRef.current || !data.length) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const margin = { top: 60, right: 200, bottom: 120, left: 150 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    // Add definitions for gradients and filters
    const defs = svg.append('defs');

    // Glow filter for points (on hover)
    const glowFilter = defs.append('filter')
      .attr('id', 'glow')
      .attr('x', '-50%')
      .attr('y', '-50%')
      .attr('width', '200%')
      .attr('height', '200%');
    
    glowFilter.append('feGaussianBlur')
      .attr('stdDeviation', '4')
      .attr('result', 'coloredBlur');
    
    const glowMerge = glowFilter.append('feMerge');
    glowMerge.append('feMergeNode').attr('in', 'coloredBlur');
    glowMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    // Determine configurations
    let xDomain: string[];
    let yDomain: string[];
    let xLabel: string;
    let yLabel: string;
    let colorKey: string;
    let colorMap: Record<string, { bg: string; dot: string; glow: string }>;
    let cellColors: string[][];

    if (type === 'trend') {
      xDomain = PROGRESS_LEVELS;
      yDomain = EXPECTATION_LEVELS;
      xLabel = 'Progress Horizon →';
      yLabel = '← Expectation Level';
      colorKey = 'adoption';
      colorMap = ADOPTION_COLORS;
      cellColors = CELL_GRADIENTS.trend;
    } else {
      xDomain = URGENCY_LEVELS;
      yDomain = CRITICALITY_LEVELS;
      xLabel = 'Urgency →';
      yLabel = '← Criticality';
      colorKey = 'actionability';
      colorMap = ACTIONABILITY_COLORS;
      cellColors = CELL_GRADIENTS.stake;
    }

    const xScale = d3.scaleBand()
      .domain(xDomain)
      .range([0, innerWidth])
      .padding(0.08);

    const yScale = d3.scaleBand()
      .domain(yDomain)
      .range([innerHeight, 0])
      .padding(0.08);

    // Draw matrix cells with gradients
    const cellGroup = g.append('g').attr('class', 'cells');
    
    yDomain.forEach((yVal, yi) => {
      xDomain.forEach((xVal, xi) => {
        const x = xScale(xVal) || 0;
        const y = yScale(yVal) || 0;
        const cellWidth = xScale.bandwidth();
        const cellHeight = yScale.bandwidth();

        // Create gradient for each cell
        const gradientId = `cell-gradient-${yi}-${xi}`;
        const gradient = defs.append('linearGradient')
          .attr('id', gradientId)
          .attr('x1', '0%')
          .attr('y1', '0%')
          .attr('x2', '100%')
          .attr('y2', '100%');
        
        gradient.append('stop')
          .attr('offset', '0%')
          .attr('stop-color', cellColors[yi][xi])
          .attr('stop-opacity', 0.6);
        
        gradient.append('stop')
          .attr('offset', '100%')
          .attr('stop-color', cellColors[yi][xi])
          .attr('stop-opacity', 0.3);

        cellGroup.append('rect')
          .attr('x', x)
          .attr('y', y)
          .attr('width', cellWidth)
          .attr('height', cellHeight)
          .attr('rx', 12)
          .attr('ry', 12)
          .attr('fill', `url(#${gradientId})`)
          .attr('stroke', '#e2e8f0')
          .attr('stroke-width', 1.5);
      });
    });

    // Add subtle inner grid
    const gridGroup = g.append('g').attr('class', 'grid');
    
    xDomain.forEach((xVal) => {
      const x = (xScale(xVal) || 0) + xScale.bandwidth() / 2;
      gridGroup.append('line')
        .attr('x1', x)
        .attr('x2', x)
        .attr('y1', -10)
        .attr('y2', innerHeight + 10)
        .attr('stroke', '#cbd5e1')
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '4,4')
        .attr('opacity', 0.5);
    });

    yDomain.forEach((yVal) => {
      const y = (yScale(yVal) || 0) + yScale.bandwidth() / 2;
      gridGroup.append('line')
        .attr('x1', -10)
        .attr('x2', innerWidth + 10)
        .attr('y1', y)
        .attr('y2', y)
        .attr('stroke', '#cbd5e1')
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '4,4')
        .attr('opacity', 0.5);
    });

    // Create axes
    const xAxisG = g.append('g')
      .attr('transform', `translate(0,${innerHeight + 20})`);

    xDomain.forEach((label, i) => {
      const x = (xScale(label) || 0) + xScale.bandwidth() / 2;
      xAxisG.append('text')
        .attr('x', x)
        .attr('y', 0)
        .attr('text-anchor', 'middle')
        .attr('font-size', '13px')
        .attr('font-weight', '600')
        .attr('fill', '#475569')
        .text(label.split(' ')[0])
        .append('tspan')
        .attr('x', x)
        .attr('dy', '1.2em')
        .attr('font-size', '11px')
        .attr('font-weight', '400')
        .attr('fill', '#64748b')
        .text(label.includes('(') ? label.match(/\(([^)]+)\)/)?.[1] || '' : label.split(' ').slice(1).join(' '));
    });

    const yAxisG = g.append('g')
      .attr('transform', `translate(-35,0)`);

    yDomain.forEach((label) => {
      const y = (yScale(label) || 0) + yScale.bandwidth() / 2;
      yAxisG.append('text')
        .attr('x', 0)
        .attr('y', y)
        .attr('text-anchor', 'end')
        .attr('dominant-baseline', 'middle')
        .attr('font-size', '13px')
        .attr('font-weight', '600')
        .attr('fill', '#475569')
        .text(label);
    });

    // Axis labels with arrows
    g.append('text')
      .attr('x', innerWidth / 2)
      .attr('y', innerHeight + 75)
      .attr('text-anchor', 'middle')
      .attr('font-size', '15px')
      .attr('font-weight', '700')
      .attr('fill', '#1e293b')
      .attr('letter-spacing', '0.5px')
      .text(xLabel);

    g.append('text')
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerHeight / 2)
      .attr('y', -110)
      .attr('text-anchor', 'middle')
      .attr('font-size', '15px')
      .attr('font-weight', '700')
      .attr('fill', '#1e293b')
      .attr('letter-spacing', '0.5px')
      .text(yLabel);

    // Filter valid data
    const validData = data.filter((d: any) => {
      if (type === 'trend') {
        return d.expectation && d.progress;
      }
      return d.criticality && d.urgency;
    });

    // Position points with better dispersion
    const getPosition = (d: any, scale: d3.ScaleBand<string>, value: string, index: number) => {
      const base = (scale(value) || 0) + scale.bandwidth() / 2;
      const jitter = (Math.sin(index * 3.2) * 0.4 + (Math.cos(index * 1.7) * 0.3) + (Math.random() - 0.5) * 0.15) * scale.bandwidth() * 0.55;
      return base + jitter;
    };

    // Draw points with beautiful styling
    const pointsGroup = g.append('g').attr('class', 'points');

    validData.forEach((d: any, i) => {
      const colorValue = d[colorKey];
      const colors = colorMap[colorValue] || { bg: '#f1f5f9', dot: '#64748b', glow: 'rgba(100, 116, 139, 0.4)' };
      
      const cx = getPosition(d, xScale, type === 'trend' ? d.progress : d.urgency, i);
      const cy = getPosition(d, yScale, type === 'trend' ? d.expectation : d.criticality, i * 1.7);

      // Outer glow ring
      const glowRing = pointsGroup.append('circle')
        .attr('cx', cx)
        .attr('cy', cy)
        .attr('r', 0)
        .attr('fill', 'none')
        .attr('stroke', colors.dot)
        .attr('stroke-width', 2)
        .attr('opacity', 0);

      // Main point
      const point = pointsGroup.append('circle')
        .attr('class', 'point')
        .attr('cx', cx)
        .attr('cy', cy)
        .attr('r', 0)
        .attr('fill', colors.dot)
        .attr('stroke', '#fff')
        .attr('stroke-width', 3)
        .style('cursor', 'pointer');

      // Animate in
      point.transition()
        .duration(600)
        .delay(i * 30)
        .ease(d3.easeElasticOut.amplitude(1).period(0.5))
        .attr('r', 10);

      // Interactions
      point
        .on('mouseenter', function(event) {
          d3.select(this)
            .transition()
            .duration(200)
            .attr('r', 14)
            .attr('filter', 'url(#glow)');

          glowRing
            .transition()
            .duration(200)
            .attr('r', 22)
            .attr('opacity', 0.5);

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
        .on('mouseleave', function() {
          d3.select(this)
            .transition()
            .duration(200)
            .attr('r', 10)
            .attr('filter', null);

          glowRing
            .transition()
            .duration(200)
            .attr('r', 0)
            .attr('opacity', 0);

          setTooltip(prev => ({ ...prev, visible: false }));
        })
        .on('click', () => {
          if (onPointClick) {
            onPointClick(d.id);
          }
        });
    });

    // Legend
    const legend = g.append('g')
      .attr('transform', `translate(${innerWidth + 40}, 20)`);

    // Legend background
    legend.append('rect')
      .attr('x', -15)
      .attr('y', -15)
      .attr('width', 160)
      .attr('height', Object.keys(colorMap).length * 32 + 50)
      .attr('rx', 12)
      .attr('fill', '#f8fafc')
      .attr('stroke', '#e2e8f0')
      .attr('stroke-width', 1);

    legend.append('text')
      .attr('x', 0)
      .attr('y', 5)
      .attr('font-size', '13px')
      .attr('font-weight', '700')
      .attr('fill', '#1e293b')
      .attr('letter-spacing', '0.5px')
      .text(type === 'trend' ? 'Adoption Stage' : 'Actionability');

    Object.entries(colorMap).forEach(([key, colors], i) => {
      const legendItem = legend.append('g')
        .attr('transform', `translate(0, ${35 + i * 32})`);

      legendItem.append('circle')
        .attr('cx', 10)
        .attr('cy', 0)
        .attr('r', 8)
        .attr('fill', colors.dot)
        .attr('stroke', '#fff')
        .attr('stroke-width', 2);

      legendItem.append('text')
        .attr('x', 26)
        .attr('y', 4)
        .attr('font-size', '11px')
        .attr('font-weight', '500')
        .attr('fill', '#475569')
        .text(key.length > 18 ? key.substring(0, 18) + '...' : key);
    });

  }, [data, type, width, height, onPointClick]);

  return (
    <div className="relative inline-block">
      <svg 
        ref={svgRef} 
        width={width} 
        height={height}
        className="overflow-visible"
        style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
      />
      
      {/* Custom Tooltip */}
      {tooltip.visible && tooltip.data && (
        <div
          className="absolute pointer-events-none z-50 animate-in fade-in-0 zoom-in-95 duration-200"
          style={{
            left: tooltip.x + 15,
            top: tooltip.y - 10,
            transform: tooltip.x > width - 250 ? 'translateX(-110%)' : 'none',
          }}
        >
          <div className="bg-white/95 backdrop-blur-sm rounded-xl shadow-2xl border border-slate-200/80 p-4 max-w-[280px]">
            <div className="font-bold text-slate-900 text-sm mb-1 leading-tight">
              {tooltip.data.name}
            </div>
            <div className="text-xs text-slate-500 mb-3 leading-relaxed">
              {tooltip.data.description}
            </div>
            <div className="space-y-1.5 pt-2 border-t border-slate-100">
              {type === 'trend' ? (
                <>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Progress</span>
                    <span className="font-medium text-slate-700">{tooltip.data.progress || 'N/A'}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Expectation</span>
                    <span className="font-medium text-slate-700">{tooltip.data.expectation || 'N/A'}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Adoption</span>
                    <span className="font-medium text-slate-700">{tooltip.data.adoption || 'N/A'}</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Urgency</span>
                    <span className="font-medium text-slate-700">{tooltip.data.urgency || 'N/A'}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Criticality</span>
                    <span className="font-medium text-slate-700">{tooltip.data.criticality || 'N/A'}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Actionability</span>
                    <span className="font-medium text-slate-700">{tooltip.data.actionability || 'N/A'}</span>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ScatterPlotVisualization;
