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

export const analysisApi = {
  // Run analysis pipeline
  run: async (options?: AnalysisOptions): Promise<AnalysisTask> => {
    const response = await apiClient.post('/analysis/run', {
      run_extraction: options?.runExtraction ?? true,
      run_dimension_assessment: options?.runDimensionAssessment ?? true,
      run_macro_discovery: options?.runMacroDiscovery ?? true,
      run_strategic_clustering: options?.runStrategicClustering ?? true,
      skip_extraction: options?.skipExtraction ?? false,
      clustering_only: options?.clusteringOnly ?? false,
      force_reextract: options?.forceReextract ?? false,
      macro_similarity_threshold: options?.macroSimilarityThreshold ?? 0.72,
      cluster_similarity_threshold: options?.clusterSimilarityThreshold ?? 0.75,
      use_llm_naming: options?.useLlmNaming ?? true,
      generate_macro_descriptions: options?.generateMacroDescriptions ?? false,
      continue_on_error: options?.continueOnError ?? true,
    });
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
