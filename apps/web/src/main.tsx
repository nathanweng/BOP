import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { MindMapWindow } from './MindMapWindow';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true },
    mutations: { retry: false },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {new URLSearchParams(window.location.search).get('view') === 'mindmap' ? <MindMapWindow /> : <App />}
    </QueryClientProvider>
  </React.StrictMode>,
);
