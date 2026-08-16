import { RouterProvider } from '@tanstack/react-router';
import { Providers } from './providers';
import { router } from './router';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toaster } from './components/ui/toast';

export function App() {
  return (
    <ErrorBoundary>
      <Providers>
        <RouterProvider router={router} />
        <Toaster />
      </Providers>
    </ErrorBoundary>
  );
}
