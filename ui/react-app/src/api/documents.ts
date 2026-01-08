import apiClient from './client';
import { Document } from '../types';

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
};
