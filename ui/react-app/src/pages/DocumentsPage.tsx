import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Header } from '../components/layout';
import { LoadingState, EmptyState } from '../components/common';
import { documentsApi } from '../api';
import { Document } from '../types';
import { useSSE } from '../hooks/useSSE';
import {
  FileText,
  Upload,
  Trash2,
  Calendar,
  CheckCircle,
  X,
  File,
  Loader2,
} from 'lucide-react';
import { format } from 'date-fns';
import clsx from 'clsx';

export function DocumentsPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadDate, setUploadDate] = useState('');
  const [uploadType, setUploadType] = useState('report');
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);
  // Track document statuses locally for real-time updates
  // Using object instead of Map to ensure React detects changes
  const [documentStatuses, setDocumentStatuses] = useState<Record<number, string>>({});

  // SSE connection for real-time document status updates
  useSSE({
    url: `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/documents/events`,
    enabled: true,
    onMessage: (event) => {
      console.log('[SSE] Event received:', event);

      if (event.type === 'document_status') {
        const { document_id, status } = event;
        console.log(`[SSE] Document ${document_id} status changed to: ${status}`);

        // Update local status map (this provides instant UI update)
        // Using object spread to create new reference and trigger React re-render
        setDocumentStatuses((prev) => {
          const next = { ...prev, [document_id]: status };
          console.log(`[SSE] Updated local status map for document ${document_id}`, next);
          return next;
        });

        // Refresh the documents list when status changes (same as podcasts)
        // Backend commits BEFORE broadcasting, so DB is always up-to-date
        queryClient.invalidateQueries({ queryKey: ['documents'] });
      }

      if (event.type === 'analysis_status') {
        console.log('[SSE] Analysis status event:', event.status);
        // Refresh on analysis completion
        if (event.status === 'completed' || event.status === 'failed') {
          queryClient.invalidateQueries({ queryKey: ['documents'] });
          queryClient.invalidateQueries({ queryKey: ['analysis', 'tasks'] });
        }
      }
    },
    onError: (error) => {
      console.error('[SSE] Connection error:', error);
    },
    onOpen: () => {
      console.log('[SSE] Connection established successfully');
      // Fetch current status when SSE connects to catch any status changes that happened
      // before the connection was established (e.g., when navigating from analysis page)
      queryClient.invalidateQueries({ queryKey: ['documents'] });
    },
  });

  // Fetch documents
  const { data: rawDocuments = [], isLoading } = useQuery({
    queryKey: ['documents'],
    queryFn: documentsApi.list,
  });

  // Sort documents by created_at (newest first)
  const documents = [...rawDocuments].sort((a, b) => {
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  // Upload mutation with optimistic update
  const uploadMutation = useMutation({
    mutationFn: ({ file, title, date, type }: { file: File; title: string; date?: string; type?: string }) =>
      documentsApi.upload(file, title, date, type),
    onMutate: async ({ title, date, type }) => {
      // Close modal immediately
      setShowUploadModal(false);
      resetUploadForm();

      // Cancel outgoing refetches
      await queryClient.cancelQueries({ queryKey: ['documents'] });

      // Get current documents
      const previousDocuments = queryClient.getQueryData<Document[]>(['documents']);

      // Create optimistic document
      const optimisticDocument: Document = {
        id: Date.now(), // Temporary ID
        title,
        source_type: type,
        gcs_document_uri: '',
        gcs_transcript_uri: undefined,
        document_date: date,
        created_at: new Date().toISOString(),
      };

      // Optimistically add to list
      queryClient.setQueryData<Document[]>(['documents'], (old = []) => [
        optimisticDocument,
        ...old,
      ]);

      return { previousDocuments, optimisticId: optimisticDocument.id };
    },
    onSuccess: () => {
      // Invalidate to get real data with correct ID
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['analysis', 'stats'] });
    },
    onError: (_error, _variables, context) => {
      // Rollback on error
      if (context?.previousDocuments) {
        queryClient.setQueryData(['documents'], context.previousDocuments);
      }
      console.error('Upload failed:', _error);
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: documentsApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      setDeleteConfirm(null);
    },
  });

  const resetUploadForm = () => {
    setUploadFile(null);
    setUploadTitle('');
    setUploadDate('');
    setUploadType('report');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setUploadFile(file);
      if (!uploadTitle) {
        // Auto-fill title from filename
        setUploadTitle(file.name.replace(/\.[^/.]+$/, '').replace(/_/g, ' '));
      }
    }
  };

  const handleUpload = (e: React.FormEvent) => {
    e.preventDefault();
    if (uploadFile && uploadTitle) {
      uploadMutation.mutate({
        file: uploadFile,
        title: uploadTitle,
        date: uploadDate || undefined,
        type: uploadType,
      });
    }
  };

  // Note: Processing now happens automatically after upload
  // No manual "Process" button needed

  // Get document status from local map or document data
  const getDocumentStatus = (document: Document): string => {
    // Check SSE-updated status first (prioritize real-time updates)
    const sseStatus = documentStatuses[document.id];
    if (sseStatus) {
      console.log(`[STATUS] Document ${document.id}: Using SSE status: ${sseStatus}`);
      return sseStatus;
    }

    // Fallback to document status field if available
    if ('status' in document) {
      const dbStatus = (document as any).status;
      console.log(`[STATUS] Document ${document.id}: Using DB status: ${dbStatus}`);
      return dbStatus;
    }

    // Legacy fallback: documents should have status field after migration
    // Default to uploading if recently created (processing automatically)
    console.log(`[STATUS] Document ${document.id}: Using default status: uploading`);
    return 'uploading';
  };

  // Status badge component
  const StatusBadge = ({ status }: { status: string }) => {
    const statusConfig: Record<string, { icon: any; label: string; color: string; spinning?: boolean }> = {
      uploading: {
        icon: Loader2,
        label: 'Uploading',
        color: 'text-blue-500',
        spinning: true,
      },
      indexing: {
        icon: Loader2,
        label: 'Indexing',
        color: 'text-orange-500',
        spinning: true,
      },
      ready: {
        icon: CheckCircle,
        label: 'Ready for Analysis',
        color: 'text-green-500',
      },
      analyzing: {
        icon: Loader2,
        label: 'Analyzing',
        color: 'text-purple-500',
        spinning: true,
      },
      analyzed: {
        icon: CheckCircle,
        label: 'Analyzed',
        color: 'text-green-600',
      },
      failed: {
        icon: X,
        label: 'Failed',
        color: 'text-red-500',
      },
    };

    const config = statusConfig[status] || statusConfig.uploading;
    const Icon = config.icon;

    return (
      <span className={clsx('flex items-center gap-1 text-sm', config.color)}>
        <Icon className={clsx('w-4 h-4', config.spinning && 'animate-spin')} />
        {config.label}
      </span>
    );
  };

  const documentTypes = [
    { value: 'report', label: 'Research Report' },
    { value: 'whitepaper', label: 'Whitepaper' },
    { value: 'article', label: 'Article' },
    { value: 'presentation', label: 'Presentation' },
    { value: 'other', label: 'Other' },
  ];

  return (
    <div className="h-full flex flex-col">
      <Header
        title="Documents"
        subtitle={`${documents.length} documents in database`}
      />

      <div className="flex-1 p-6 overflow-auto">
        {/* Action Bar */}
        <div className="flex items-center justify-between mb-6">
          <button
            onClick={() => setShowUploadModal(true)}
            className="btn btn-primary flex items-center gap-2"
          >
            <Upload className="w-4 h-4" />
            Upload Document
          </button>
        </div>

        {/* Documents List */}
        {isLoading ? (
          <LoadingState message="Loading documents..." />
        ) : documents.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No documents yet"
            description="Upload PDF documents to analyze health trends from research reports and articles."
            action={
              <button
                onClick={() => setShowUploadModal(true)}
                className="btn btn-primary"
              >
                Upload Document
              </button>
            }
          />
        ) : (
          <div className="grid gap-4">
            {documents.map((doc) => {
              const status = getDocumentStatus(doc);
              const isProcessing = ['uploading', 'indexing', 'analyzing'].includes(status);
              console.log(`[RENDER] Document ${doc.id} rendering with status: ${status}`);

              return (
                <div
                  key={doc.id}
                  className="card p-4 hover:shadow-md transition-shadow"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-4">
                      <div className="w-12 h-12 bg-amber-100 rounded-lg flex items-center justify-center">
                        <FileText className="w-6 h-6 text-amber-600" />
                      </div>
                      <div>
                        <h3 className="font-medium text-gray-900">{doc.title}</h3>
                        <div className="flex items-center gap-4 mt-1 text-sm text-gray-500">
                          {doc.source_type && (
                            <span className="badge badge-info">
                              {doc.source_type}
                            </span>
                          )}
                          {doc.document_date && (
                            <span className="flex items-center gap-1">
                              <Calendar className="w-4 h-4" />
                              {format(new Date(doc.document_date), 'MMM d, yyyy')}
                            </span>
                          )}
                          <StatusBadge status={status} />
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      {deleteConfirm === doc.id ? (
                        <>
                          <span className="text-sm text-gray-500">Delete?</span>
                          <button
                            onClick={() => deleteMutation.mutate(doc.id)}
                            className="btn btn-sm btn-danger"
                          >
                            Yes
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(null)}
                            className="btn btn-sm btn-secondary"
                          >
                            No
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => setDeleteConfirm(doc.id)}
                          disabled={isProcessing || deleteMutation.isPending}
                          className="btn btn-sm btn-ghost text-red-600 hover:bg-red-50 disabled:opacity-50"
                          title="Delete document"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Upload Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => {
              setShowUploadModal(false);
              resetUploadForm();
            }}
          />
          <div className="relative bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Upload Document</h2>
              <button
                onClick={() => {
                  setShowUploadModal(false);
                  resetUploadForm();
                }}
                className="p-2 hover:bg-gray-100 rounded-lg"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleUpload} className="space-y-4">
              {/* File Upload */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  File
                </label>
                <div
                  onClick={() => fileInputRef.current?.click()}
                  className={clsx(
                    'border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors',
                    uploadFile
                      ? 'border-savencia-primary bg-savencia-primary/5'
                      : 'border-gray-300 hover:border-gray-400'
                  )}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    onChange={handleFileSelect}
                    className="hidden"
                  />
                  {uploadFile ? (
                    <div className="flex items-center justify-center gap-2">
                      <File className="w-5 h-5 text-savencia-primary" />
                      <span className="text-sm font-medium text-gray-900">
                        {uploadFile.name}
                      </span>
                    </div>
                  ) : (
                    <>
                      <Upload className="w-8 h-8 text-gray-400 mx-auto mb-2" />
                      <p className="text-sm text-gray-500">
                        Click to upload a PDF document
                      </p>
                    </>
                  )}
                </div>
              </div>

              {/* Title */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Title
                </label>
                <input
                  type="text"
                  value={uploadTitle}
                  onChange={(e) => setUploadTitle(e.target.value)}
                  placeholder="Document title"
                  className="input"
                  required
                />
              </div>

              {/* Document Type */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Document Type
                </label>
                <select
                  value={uploadType}
                  onChange={(e) => setUploadType(e.target.value)}
                  className="input"
                >
                  {documentTypes.map((type) => (
                    <option key={type.value} value={type.value}>
                      {type.label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Document Date */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Document Date (optional)
                </label>
                <input
                  type="date"
                  value={uploadDate}
                  onChange={(e) => setUploadDate(e.target.value)}
                  className="input"
                />
              </div>

              {/* Submit */}
              <div className="flex justify-end gap-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowUploadModal(false);
                    resetUploadForm();
                  }}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!uploadFile || !uploadTitle || uploadMutation.isPending}
                  className="btn btn-primary"
                >
                  {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
