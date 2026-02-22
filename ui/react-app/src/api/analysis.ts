import apiClient from './client';
import { AnalysisTask, PipelineStats, TaskStatus } from '../types';

export interface AnalysisOptions {
  runExtraction?: boolean;
  runDimensionAssessment?: boolean;
  runMacroDiscovery?: boolean;
  runStrategicClustering?: boolean;
  skipExtraction?: boolean;
  clusteringOnly?: boolean;
  forceReextract?: boolean;
  macroSimilarityThreshold?: number;
  clusterSimilarityThreshold?: number;
  useLlmNaming?: boolean;
  generateMacroDescriptions?: boolean;
  continueOnError?: boolean;
}

export interface AnalysisDefaults {
  macro_similarity_threshold: number;
  cluster_similarity_threshold: number;
  dedup_threshold: number;
  dimension_top_k: number;
  dimension_min_similarity: number;
  use_llm_naming: boolean;
  generate_macro_descriptions: boolean;
  continue_on_error: boolean;
  batch_size: number;
  max_workers: number;
  min_cluster_size: number;
  label_max_tokens: number;
  label_temperature: number;
  description_max_tokens: number;
  description_temperature: number;
}

export const analysisApi = {
  // Run analysis pipeline — only sends explicitly provided values.
  // Server resolves defaults from the central AnalysisConfig.
  run: async (options?: AnalysisOptions): Promise<AnalysisTask> => {
    const body: Record<string, unknown> = {};

    if (options?.runExtraction !== undefined) body.run_extraction = options.runExtraction;
    if (options?.runDimensionAssessment !== undefined) body.run_dimension_assessment = options.runDimensionAssessment;
    if (options?.runMacroDiscovery !== undefined) body.run_macro_discovery = options.runMacroDiscovery;
    if (options?.runStrategicClustering !== undefined) body.run_strategic_clustering = options.runStrategicClustering;
    if (options?.skipExtraction !== undefined) body.skip_extraction = options.skipExtraction;
    if (options?.clusteringOnly !== undefined) body.clustering_only = options.clusteringOnly;
    if (options?.forceReextract !== undefined) body.force_reextract = options.forceReextract;
    if (options?.macroSimilarityThreshold !== undefined) body.macro_similarity_threshold = options.macroSimilarityThreshold;
    if (options?.clusterSimilarityThreshold !== undefined) body.cluster_similarity_threshold = options.clusterSimilarityThreshold;
    if (options?.useLlmNaming !== undefined) body.use_llm_naming = options.useLlmNaming;
    if (options?.generateMacroDescriptions !== undefined) body.generate_macro_descriptions = options.generateMacroDescriptions;
    if (options?.continueOnError !== undefined) body.continue_on_error = options.continueOnError;

    const response = await apiClient.post('/analysis/run', body);
    return response.data;
  },

  // Get analysis config defaults from server
  getDefaults: async (): Promise<AnalysisDefaults> => {
    const response = await apiClient.get('/analysis/defaults');
    return response.data;
  },

  // Get task status
  getStatus: async (taskId: string): Promise<AnalysisTask> => {
    const response = await apiClient.get(`/analysis/status/${taskId}`);
    return response.data;
  },

  // List all analysis tasks
  listTasks: async (status?: TaskStatus): Promise<AnalysisTask[]> => {
    const params: Record<string, string> = {};
    if (status) params.status = status;
    const response = await apiClient.get('/analysis/tasks', { params });
    return response.data;
  },

  // Get pipeline statistics
  getStats: async (): Promise<PipelineStats> => {
    const response = await apiClient.get('/analysis/stats');
    return response.data;
  },

  // Delete a task
  deleteTask: async (taskId: string): Promise<void> => {
    await apiClient.delete(`/analysis/tasks/${taskId}`);
  },

  // Reset analysis data
  reset: async (options: {
    resetDimensions?: boolean;
    resetClustering?: boolean;
    resetInsights?: boolean;
    resetAll?: boolean;
  }): Promise<{ message: string }> => {
    const response = await apiClient.post('/analysis/reset', null, {
      params: {
        reset_dimensions: options.resetDimensions,
        reset_clustering: options.resetClustering,
        reset_insights: options.resetInsights,
        reset_all: options.resetAll,
      },
    });
    return response.data;
  },
};
