import apiClient from './client';
import { UnitInsight, MacroInsight, Cluster, InsightHierarchy, SearchResult, VisualizationData } from '../types';

export const insightsApi = {
  // List unit insights
  listUnitInsights: async (options?: {
    skip?: number;
    limit?: number;
    sourceType?: string;
    insightType?: string;
    hasMacro?: boolean;
  }): Promise<{ insights: UnitInsight[]; count: number }> => {
    const params: Record<string, unknown> = {};
    if (options?.skip) params.skip = options.skip;
    if (options?.limit) params.limit = options.limit;
    if (options?.sourceType) params.source_type = options.sourceType;
    if (options?.insightType) params.insight_type = options.insightType;
    if (options?.hasMacro !== undefined) params.has_macro = options.hasMacro;

    const response = await apiClient.get('/insights/unit', { params });
    return response.data;
  },

  // Get a unit insight with dimensions
  getUnitInsight: async (id: number): Promise<UnitInsight> => {
    const response = await apiClient.get(`/insights/unit/${id}`);
    return response.data;
  },

  // List macro insights
  listMacroInsights: async (options?: {
    skip?: number;
    limit?: number;
    clusterId?: number;
    hasCluster?: boolean;
  }): Promise<{ insights: MacroInsight[]; count: number }> => {
    const params: Record<string, unknown> = {};
    if (options?.skip) params.skip = options.skip;
    if (options?.limit) params.limit = options.limit;
    if (options?.clusterId) params.cluster_id = options.clusterId;
    if (options?.hasCluster !== undefined) params.has_cluster = options.hasCluster;

    const response = await apiClient.get('/insights/macro', { params });
    return response.data;
  },

  // Get a macro insight with unit insights
  getMacroInsight: async (id: number): Promise<MacroInsight> => {
    const response = await apiClient.get(`/insights/macro/${id}`);
    return response.data;
  },

  // List clusters
  listClusters: async (): Promise<{ clusters: Cluster[]; count: number }> => {
    const response = await apiClient.get('/insights/clusters');
    return response.data;
  },

  // Get a cluster with macro insights
  getCluster: async (id: number): Promise<Cluster> => {
    const response = await apiClient.get(`/insights/clusters/${id}`);
    return response.data;
  },

  // Get full insight hierarchy
  getHierarchy: async (): Promise<InsightHierarchy> => {
    const response = await apiClient.get('/insights/hierarchy');
    return response.data;
  },

  // Semantic search
  search: async (query: string, topK = 10, sourceType?: string): Promise<{ query: string; results: SearchResult[]; count: number }> => {
    const response = await apiClient.post('/insights/search', {
      query,
      top_k: topK,
      source_type: sourceType,
    });
    return response.data;
  },

  // Get visualization data
  getVisualizationData: async (): Promise<VisualizationData> => {
    const response = await apiClient.get('/insights/visualization-data');
    return response.data;
  },
};
