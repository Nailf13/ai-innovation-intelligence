import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Header } from '../components/layout';
import { StatusBadge, LoadingState, EmptyState } from '../components/common';
import { analysisApi, ingestionApi } from '../api';
import { AnalysisTask, IngestionTask, PipelineStats } from '../types';
import {
  Play,
  RefreshCw,
  Trash2,
  Clock,
  CheckCircle,
  XCircle,
  BarChart3,
  Layers,
  FileText,
  Mic,
  AlertTriangle,
  Activity,
} from 'lucide-react';
import { format, formatDistanceToNow } from 'date-fns';
import clsx from 'clsx';

export function PipelinesPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'active' | 'history'>('active');

  // Fetch analysis tasks
  const { data: analysisTasks = [], isLoading: loadingAnalysis } = useQuery({
    queryKey: ['analysis', 'tasks'],
    queryFn: () => analysisApi.listTasks(),
    refetchInterval: 5000, // Poll every 5 seconds for running tasks
  });

  // Fetch ingestion tasks
  const { data: ingestionTasks = [], isLoading: loadingIngestion } = useQuery({
    queryKey: ['ingestion', 'tasks'],
    queryFn: () => ingestionApi.listTasks(),
    refetchInterval: 5000,
  });

  // Fetch stats
  const { data: stats } = useQuery({
    queryKey: ['analysis', 'stats'],
    queryFn: analysisApi.getStats,
  });

  // Run analysis mutation
  const runAnalysisMutation = useMutation({
    mutationFn: analysisApi.run,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analysis', 'tasks'] });
    },
  });


  // Delete task mutation
  const deleteTaskMutation = useMutation({
    mutationFn: async ({ taskId, type }: { taskId: string; type: 'analysis' | 'ingestion' }) => {
      if (type === 'analysis') {
        await analysisApi.deleteTask(taskId);
      } else {
        await ingestionApi.deleteTask(taskId);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analysis', 'tasks'] });
      queryClient.invalidateQueries({ queryKey: ['ingestion', 'tasks'] });
    },
  });

  const allTasks = [
    ...analysisTasks.map((t) => ({ ...t, _type: 'analysis' as const })),
    ...ingestionTasks.map((t) => ({ ...t, _type: 'ingestion' as const })),
  ];

  const activeTasks = allTasks.filter((t) => t.status === 'running' || t.status === 'pending');
  const completedTasks = allTasks.filter((t) => t.status === 'completed' || t.status === 'failed');

  const isLoading = loadingAnalysis || loadingIngestion;

  return (
    <div className="h-full flex flex-col">
      <Header
        title="Pipelines"
        subtitle="Monitor and manage analysis pipelines"
      />

      <div className="flex-1 p-6 overflow-auto">
        {/* Stats Cards - Only showing items ready for analysis */}
        <div className="grid grid-cols-2 gap-4 mb-6">
          <StatCard
            icon={Mic}
            label="Podcasts Ready for Analysis"
            value={stats?.podcast_episodes || 0}
            subtitle={`${stats?.podcasts_analyzed || 0} analyzed`}
            color="purple"
          />
          <StatCard
            icon={FileText}
            label="Documents Ready for Analysis"
            value={stats?.documents || 0}
            subtitle={`${stats?.documents_analyzed || 0} analyzed`}
            color="amber"
          />
        </div>

        {/* Helper text explaining "Ready" counts */}
        <div className="text-sm text-gray-500 mb-4">
          <strong>Ready for Analysis:</strong> Content that has been transcribed and indexed, but not yet analyzed (no insights extracted).
          <br />
          These counts are the source of truth for items available for the analysis pipeline.
        </div>

        {/* Actions */}
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={() => runAnalysisMutation.mutate({})}
            disabled={runAnalysisMutation.isPending}
            className="btn btn-primary flex items-center gap-2"
          >
            <Play className="w-4 h-4" />
            Run Full Analysis
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-4 border-b">
          <button
            onClick={() => setActiveTab('active')}
            className={clsx(
              'px-4 py-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'active'
                ? 'border-savencia-primary text-savencia-primary'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            Active ({activeTasks.length})
          </button>
          <button
            onClick={() => setActiveTab('history')}
            className={clsx(
              'px-4 py-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'history'
                ? 'border-savencia-primary text-savencia-primary'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            History ({completedTasks.length})
          </button>
        </div>

        {/* Task List */}
        {isLoading ? (
          <LoadingState message="Loading pipelines..." />
        ) : activeTab === 'active' ? (
          activeTasks.length === 0 ? (
            <EmptyState
              icon={Activity}
              title="No active pipelines"
              description="Start an analysis or indexing pipeline to see progress here."
            />
          ) : (
            <div className="space-y-4">
              {activeTasks.map((task) => (
                <TaskCard
                  key={task.task_id}
                  task={task}
                  onDelete={() =>
                    deleteTaskMutation.mutate({
                      taskId: task.task_id,
                      type: task._type,
                    })
                  }
                />
              ))}
            </div>
          )
        ) : completedTasks.length === 0 ? (
          <EmptyState
            icon={Clock}
            title="No pipeline history"
            description="Completed pipelines will appear here."
          />
        ) : (
          <div className="space-y-4">
            {completedTasks.map((task) => (
              <TaskCard
                key={task.task_id}
                task={task}
                onDelete={() =>
                  deleteTaskMutation.mutate({
                    taskId: task.task_id,
                    type: task._type,
                  })
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface StatCardProps {
  icon: typeof Mic;
  label: string;
  value: number;
  subtitle?: string;
  color: 'purple' | 'amber' | 'blue' | 'green';
}

function StatCard({ icon: Icon, label, value, subtitle, color }: StatCardProps) {
  const colors = {
    purple: 'bg-purple-100 text-purple-600',
    amber: 'bg-amber-100 text-amber-600',
    blue: 'bg-blue-100 text-blue-600',
    green: 'bg-green-100 text-green-600',
  };

  return (
    <div className="card p-4">
      <div className="flex items-center gap-3">
        <div className={clsx('w-10 h-10 rounded-lg flex items-center justify-center', colors[color])}>
          <Icon className="w-5 h-5" />
        </div>
        <div>
          <p className="text-2xl font-semibold text-gray-900">{value}</p>
          <p className="text-sm text-gray-500">{label}</p>
          {subtitle && <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>}
        </div>
      </div>
    </div>
  );
}

interface TaskCardProps {
  task: (AnalysisTask | IngestionTask) & { _type: 'analysis' | 'ingestion' };
  onDelete: () => void;
}

function TaskCard({ task, onDelete }: TaskCardProps) {
  const isAnalysis = task._type === 'analysis';
  const analysisTask = isAnalysis ? (task as AnalysisTask) : null;

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <StatusBadge status={task.status} />
            <span className="text-sm text-gray-500">
              {isAnalysis ? 'Analysis Pipeline' : 'Ingestion Pipeline'}
            </span>
            <span className="text-xs text-gray-400 font-mono">
              {task.task_id}
            </span>
          </div>

          {/* Progress for running tasks */}
          {task.status === 'running' && analysisTask && (
            <div className="mb-3">
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="text-gray-600">
                  {analysisTask.current_stage || 'Processing...'}
                </span>
                <span className="text-gray-500">
                  {Math.round(analysisTask.progress * 100)}%
                </span>
              </div>
              <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-savencia-primary rounded-full transition-all"
                  style={{ width: `${analysisTask.progress * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Results for completed tasks */}
          {task.status === 'completed' && analysisTask?.result && (
            <div className="grid grid-cols-4 gap-4 text-sm">
              {Object.entries(analysisTask.result.stages || {}).map(([name, stage]) => (
                <div key={name} className="bg-gray-50 rounded-lg p-2">
                  <p className="font-medium text-gray-900 capitalize">{name}</p>
                  <p className="text-gray-500">
                    {stage.items_created} created
                  </p>
                  <p className="text-xs text-gray-400">
                    {stage.duration_seconds.toFixed(1)}s
                  </p>
                </div>
              ))}
            </div>
          )}

          {/* Error for failed tasks */}
          {task.status === 'failed' && task.error && (
            <div className="flex items-start gap-2 mt-2 p-3 bg-red-50 rounded-lg">
              <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-700">{task.error}</p>
            </div>
          )}
        </div>

        {/* Delete button for completed/failed tasks */}
        {(task.status === 'completed' || task.status === 'failed') && (
          <button
            onClick={onDelete}
            className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}
