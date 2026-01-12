import apiClient from './client';
import { Document, IngestionTask } from '../types';

export const documentsApi = {
  // List all documents
  list: async (): Promise<Document[]> => {
    const response = await apiClient.get('/documents/');
    return response.data.documents || [];
  },

  // Get a single document
  get: async (id: number): Promise<Document> => {
    const response = await apiClient.get(`/documents/${id}`);
    return response.data;
  },

  // Upload a document
  upload: async (
    file: File,
    title: string,
    documentDate?: string,
    sourceType?: string
  ): Promise<Document> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('title', title);
    if (documentDate) formData.append('document_date', documentDate);
    if (sourceType) formData.append('source_type', sourceType);

    const response = await apiClient.post('/documents/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  // Delete a document
  delete: async (id: number): Promise<void> => {
    await apiClient.delete(`/documents/${id}`);
  },

  // Process document (index for vector search)
  processDocument: async (documentId: number): Promise<IngestionTask> => {
    const response = await apiClient.post(`/documents/process/${documentId}`);
    return response.data;
  },

  // Get processing status
  getProcessingStatus: async (taskId: string): Promise<IngestionTask> => {
    const response = await apiClient.get(`/documents/process/status/${taskId}`);
    return response.data;
  },

  // Check if document has been analyzed
  checkAnalyzed: async (documentId: number): Promise<{ analyzed: boolean; insight_count: number }> => {
    const response = await apiClient.get(`/documents/${documentId}/analyzed`);
    return response.data;
  },

  // Get document processing status
  getDocumentProcessingStatus: async (documentId: number): Promise<{ status: string }> => {
    const response = await apiClient.get(`/documents/${documentId}/processing-status`);
    return response.data;
  },
};
