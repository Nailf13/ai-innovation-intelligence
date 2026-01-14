import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Header } from '../components/layout';
import { LoadingState, EmptyState } from '../components/common';
import { documentsApi } from '../api';
import { Document } from '../types';
import {
  FileText,
  Upload,
  Trash2,
  Calendar,
  CheckCircle,
  X,
  File,
  AlertCircle,
  Loader2,
  Play,
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
  const [processingIds, setProcessingIds] = useState<Set<number>>(new Set());
  const processingPolling = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());

  // Cleanup polling intervals on unmount
  useEffect(() => {
    return () => {
      processingPolling.current.forEach((interval) => clearInterval(interval));
    };
  }, []);

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
    onMutate: async ({ file, title, date, type }) => {
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
    onSuccess: (data, variables, context) => {
      // Invalidate to get real data with correct ID
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['analysis', 'stats'] });
    },
    onError: (error, variables, context) => {
      // Rollback on error
      if (context?.previousDocuments) {
        queryClient.setQueryData(['documents'], context.previousDocuments);
      }
      console.error('Upload failed:', error);
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

  // Start polling for processing completion (index for vector search)
  const startProcessingPolling = (documentId: number, taskId: string) => {
    // Clear any existing interval for this document
    const existing = processingPolling.current.get(documentId);
    if (existing) clearInterval(existing);

    const interval = setInterval(async () => {
      try {
        const status = await documentsApi.getProcessingStatus(taskId);

        if (status.status === 'completed' || status.status === 'failed') {
          // Processing finished, stop polling and update UI
          clearInterval(interval);
          processingPolling.current.delete(documentId);
          setProcessingIds((prev) => {
            const next = new Set(prev);
            next.delete(documentId);
            return next;
          });

          // Invalidate both documents and stats queries
          queryClient.invalidateQueries({ queryKey: ['documents'] });
          queryClient.invalidateQueries({ queryKey: ['analysis', 'stats'] });

          if (status.status === 'failed') {
            console.error('Processing failed:', status.error);
          }
        }
      } catch (error) {
        console.error('Error polling processing status:', error);
      }
    }, 3000); // Poll every 3 seconds

    processingPolling.current.set(documentId, interval);

    // Stop polling after 10 minutes
    setTimeout(() => {
      const int = processingPolling.current.get(documentId);
      if (int) {
        clearInterval(int);
        processingPolling.current.delete(documentId);
        setProcessingIds((prev) => {
          const next = new Set(prev);
          next.delete(documentId);
          return next;
        });
      }
    }, 10 * 60 * 1000);
  };

  // Process handler (index for vector search)
  const handleProcess = async (documentId: number) => {
    setProcessingIds((prev) => new Set(prev).add(documentId));
    try {
      const task = await documentsApi.processDocument(documentId);
      // Start polling for completion
      startProcessingPolling(documentId, task.task_id);
    } catch (error) {
      console.error('Error starting processing:', error);
      setProcessingIds((prev) => {
        const next = new Set(prev);
        next.delete(documentId);
        return next;
      });
    }
  };

  // Get document status from backend (cached with React Query)
  const useDocumentStatus = (documentId: number) => {
    // Check if this is an optimistic ID (timestamps are much larger than DB IDs)
    const isOptimisticId = documentId > 1000000000000; // IDs from Date.now() are > 1 trillion

    return useQuery({
      queryKey: ['document', documentId, 'status'],
      queryFn: () => documentsApi.getDocumentProcessingStatus(documentId),
      enabled: !isOptimisticId, // Don't query for optimistic IDs
      refetchInterval: (query) => {
        // Poll more frequently if processing
        const status = query.state.data?.status;
        if (status === 'processing' || status === 'uploading') return 3000;
        // Otherwise poll less frequently
        return 10000;
      },
    });
  };

  // Status helper functions based on backend status
  const getStatusInfo = (statusResponse: { status: string } | undefined) => {
    if (!statusResponse) return { status: 'loading', label: 'Loading...', needsAction: false, isProcessing: false };

    const { status } = statusResponse;

    switch (status) {
      case 'analyzed':
        return { status: 'analyzed', label: 'Analyzed', needsAction: false, isProcessing: false };
      case 'ready':
        return { status: 'ready', label: 'Ready to analyze', needsAction: false, isProcessing: false };
      case 'processing':
        return { status: 'processing', label: 'Indexing...', needsAction: false, isProcessing: true };
      case 'uploading':
        return { status: 'uploading', label: 'Uploading...', needsAction: false, isProcessing: true };
      default:
        return { status: 'unknown', label: 'Unknown', needsAction: false, isProcessing: false };
    }
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
            {documents.map((doc) => (
              <DocumentCard
                key={doc.id}
                document={doc}
                onProcess={handleProcess}
                onDelete={() => setDeleteConfirm(doc.id)}
                deleteConfirm={deleteConfirm === doc.id}
                onCancelDelete={() => setDeleteConfirm(null)}
                onConfirmDelete={() => deleteMutation.mutate(doc.id)}
                getStatusInfo={getStatusInfo}
                useDocumentStatus={useDocumentStatus}
              />
            ))}
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

// Separate component for document card that fetches its own status
interface DocumentCardProps {
  document: Document;
  onProcess: (id: number) => Promise<void>;
  onDelete: () => void;
  deleteConfirm: boolean;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  getStatusInfo: (statusResponse: { status: string } | undefined) => {
    status: string;
    label: string;
    needsAction: boolean;
    isProcessing: boolean;
  };
  useDocumentStatus: (documentId: number) => {
    data: { status: string } | undefined;
    isLoading: boolean;
  };
}

function DocumentCard({
  document,
  onProcess,
  onDelete,
  deleteConfirm,
  onCancelDelete,
  onConfirmDelete,
  getStatusInfo,
  useDocumentStatus,
}: DocumentCardProps) {
  const [isProcessingLocally, setIsProcessingLocally] = useState(false);
  const { data: statusData, isLoading: statusLoading } = useDocumentStatus(document.id);

  // Check if this is an optimistic document
  const isOptimistic = document.id > 1000000000000;

  // For optimistic documents, show uploading status
  const statusInfo = isOptimistic
    ? { status: 'uploading', label: 'Uploading...', needsAction: false, isProcessing: true }
    : getStatusInfo(statusData);

  const queryClient = useQueryClient();

  const handleProcessClick = async () => {
    setIsProcessingLocally(true);
    await onProcess(document.id);
    // Invalidate status query to immediately refetch
    queryClient.invalidateQueries({ queryKey: ['document', document.id, 'status'] });
  };

  // Reset local processing state when backend status changes to processing/completed
  useEffect(() => {
    if (statusInfo.isProcessing || statusInfo.status === 'ready' || statusInfo.status === 'analyzed') {
      setIsProcessingLocally(false);
    }
  }, [statusInfo.isProcessing, statusInfo.status]);

  // Determine if button should be shown
  const shouldShowProcessButton = statusInfo.needsAction && !isProcessingLocally && !statusInfo.isProcessing;

  // Status icon and color
  const getStatusIcon = () => {
    switch (statusInfo.status) {
      case 'analyzed':
        return <CheckCircle className="w-4 h-4 text-blue-500" />;
      case 'ready':
        return <CheckCircle className="w-4 h-4 text-green-500" />;
      case 'processing':
      case 'uploading':
        return <Loader2 className="w-4 h-4 text-yellow-500 animate-spin" />;
      case 'loading':
        return <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />;
      default:
        return <AlertCircle className="w-4 h-4 text-gray-400" />;
    }
  };

  return (
    <div className="card p-4 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 bg-amber-100 rounded-lg flex items-center justify-center">
            <FileText className="w-6 h-6 text-amber-600" />
          </div>
          <div>
            <h3 className="font-medium text-gray-900">{document.title}</h3>
            <div className="flex items-center gap-4 mt-1 text-sm text-gray-500">
              {document.source_type && (
                <span className="badge badge-info">
                  {document.source_type}
                </span>
              )}
              {document.document_date && (
                <span className="flex items-center gap-1">
                  <Calendar className="w-4 h-4" />
                  {format(new Date(document.document_date), 'MMM d, yyyy')}
                </span>
              )}
              <span className="flex items-center gap-1">
                {statusLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Loading status...
                  </>
                ) : (
                  <>
                    {getStatusIcon()}
                    {statusInfo.label}
                  </>
                )}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {shouldShowProcessButton && (
            <button
              onClick={handleProcessClick}
              className="btn btn-sm btn-primary flex items-center gap-1 mr-2"
            >
              <Play className="w-4 h-4" />
              Process
            </button>
          )}

          {deleteConfirm ? (
            <>
              <span className="text-sm text-gray-500">Delete?</span>
              <button
                onClick={onConfirmDelete}
                className="btn btn-sm btn-danger"
              >
                Yes
              </button>
              <button
                onClick={onCancelDelete}
                className="btn btn-sm btn-secondary"
              >
                No
              </button>
            </>
          ) : (
            <button
              onClick={onDelete}
              className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
