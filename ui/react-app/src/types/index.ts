// API Types

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed';

export type InsightType = 'trend' | 'health_stake';

export type AdoptionLevel = 'Nascent experimentation' | 'Early adoption' | 'Crossing the chasm' | 'Established practice';

export type ExpectationLevel = 'Low' | 'Moderate' | 'High';

export type ProgressHorizon = 'Near-term (0-12 months)' | 'Mid-term (1-3 years)' | 'Long-term (3+ years)';

export type CriticalityLevel = 'Low' | 'Moderate' | 'High';

export type UrgencyLevel = 'Long-term' | 'Mid-term' | 'Immediate';

export type ActionabilityLevel = 'Hard to address' | 'Moderately addressable' | 'Highly addressable';

// Podcast Types
export interface PodcastSearchResult {
  feed_id: number;
  title: string;
  author?: string;
  description?: string;
  image_url?: string;
  url?: string;
  episode_count?: number;
}

export interface PodcastEpisode {
  id: number;
  podcast_name: string;
  episode_title: string;
  audio_url?: string;
  gcs_audio_uri?: string;
  gcs_transcript_uri?: string;
  audio_path?: string;
  transcript_path?: string;
  episode_date?: string;
  created_at: string;
}

export interface PodcastEpisodeInfo {
  id: number;
  title: string;
  description?: string;
  date_published?: string;
  duration?: number;
  audio_url: string;
  image_url?: string;
}

export interface EpisodeListResponse {
  feed_id: number;
  episodes: PodcastEpisodeInfo[];
  count: number;
  has_more: boolean;
  next_offset?: number;
}

// Document Types
export interface Document {
  id: number;
  title: string;
  source_type?: string;
  gcs_document_uri?: string;
  gcs_transcript_uri?: string;
  transcript_path?: string;
  document_date?: string;
  created_at: string;
}

// Ingestion Types
export interface IngestionTask {
  task_id: string;
  task_type: string;
  status: TaskStatus;
  entity_id?: number;
  current_stage?: string;
  progress?: number;
  error?: string;
  result?: Record<string, unknown>;
}

// Analysis Types
export interface AnalysisTask {
  task_id: string;
  status: TaskStatus;
  current_stage?: string;
  progress: number;
  error?: string;
  result?: {
    success: boolean;
    stages: Record<string, StageResult>;
    total_duration_seconds: number;
  };
}

export interface StageResult {
  success: boolean;
  items_processed: number;
  items_created: number;
  duration_seconds: number;
  error?: string;
}

export interface PipelineStats {
  podcast_episodes: number;  // Ready for analysis (not analyzed yet)
  podcast_episodes_total: number;
  podcasts_analyzed: number;  // Analyzed count
  documents: number;  // Ready for analysis (not analyzed yet)
  documents_total: number;
  documents_analyzed: number;  // Analyzed count
  unit_insights: {
    total: number;
    trends: number;
    health_stakes: number;
    with_macro: number;
    without_macro: number;
  };
  macro_insights: {
    total: number;
    with_cluster: number;
    without_cluster: number;
  };
  clusters: {
    total: number;
    by_name: Record<string, number>;
  };
  dimensions_assessed: number;
}

// Insight Types
export interface EvidenceItem {
  text: string;
  source_ref?: string;
  similarity_score?: number;
  // Podcast metadata
  start_time?: number;
  end_time?: number;
  // Document metadata
  page?: number;
  section?: string;
}

export interface Dimension {
  id?: number;
  dimension_type: 'adoption' | 'expectation' | 'progress' | 'criticality' | 'urgency' | 'actionability';
  value: string;
  confidence?: number;
  evidence: EvidenceItem[];
}

export interface UnitInsight {
  id: number;
  name: string;
  description: string;
  type: InsightType;
  source_type: 'podcast' | 'document';
  source_id?: number;
  macro_insight_id?: number;
  dimensions: Dimension[];
  created_at?: string;
}

export interface MacroInsight {
  id: number;
  name: string;
  description?: string;
  cluster_id?: number;
  unit_insight_count: number;
  unit_insights?: UnitInsight[];
  created_at?: string;
}

export interface Cluster {
  id: number;
  name: string;
  description?: string;
  macro_insight_count: number;
  macro_insights?: MacroInsight[];
}

export interface InsightHierarchy {
  clusters: Array<{
    id: number;
    name: string;
    description?: string;
    macro_insights: Array<{
      id: number;
      name: string;
      description?: string;
      unit_insights: Array<{
        id: number;
        name: string;
        type: InsightType;
      }>;
    }>;
    orphan_unit_insights: Array<{
      id: number;
      name: string;
      type: InsightType;
    }>;
  }>;
  unassigned_macro_insights: Array<{
    id: number;
    name: string;
    description?: string;
    unit_insights: Array<{
      id: number;
      name: string;
      type: InsightType;
    }>;
  }>;
  orphan_unit_insights: Array<{
    id: number;
    name: string;
    type: InsightType;
  }>;
}

// Search Types
export interface SearchResult {
  chunk_id: number;
  source_type: string;
  text: string;
  similarity: number;
  metadata: Record<string, unknown>;
}

// API Response Types
export interface ListResponse<T> {
  count: number;
  items?: T[];
  insights?: T[];
  documents?: T[];
  clusters?: T[];
  results?: T[];
}

// Visualization Types
export interface TrendVisualizationPoint {
  id: number;
  name: string;
  description: string;
  expectation?: ExpectationLevel | null;
  progress?: ProgressHorizon | null;
  adoption?: AdoptionLevel | null;
}

export interface StakeVisualizationPoint {
  id: number;
  name: string;
  description: string;
  criticality?: CriticalityLevel | null;
  urgency?: UrgencyLevel | null;
  actionability?: ActionabilityLevel | null;
}

export interface VisualizationData {
  trends: TrendVisualizationPoint[];
  stakes: StakeVisualizationPoint[];
}
