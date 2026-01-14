import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SignedIn, SignedOut, SignIn } from '@clerk/clerk-react';
import { Layout } from './components/layout';
import { PodcastsPage, DocumentsPage, PipelinesPage, InsightsPage } from './pages';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SignedIn>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<Navigate to="/insights" replace />} />
              <Route path="insights" element={<InsightsPage />} />
              <Route path="podcasts" element={<PodcastsPage />} />
              <Route path="documents" element={<DocumentsPage />} />
              <Route path="pipelines" element={<PipelinesPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </SignedIn>
      <SignedOut>
        <div className="flex items-center justify-center min-h-screen bg-gray-50">
          <SignIn />
        </div>
      </SignedOut>
    </QueryClientProvider>
  );
}

export default App;
