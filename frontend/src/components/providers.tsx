"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { SessionProvider } from "@/lib/session";

export function Providers({ children }: { children: React.ReactNode }) {
  // One client per browser session. Creating it in state rather than at module
  // scope keeps it out of the server render, where a shared cache would leak
  // one request's data into another's.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // A refusal will not become an approval by retrying, and retrying
            // auth failures just delays the error the user needs to see.
            retry: (count, error) => {
              const status = (error as { status?: number }).status;
              if (status && status >= 400 && status < 500) return false;
              return count < 2;
            },
            refetchOnWindowFocus: false,
            staleTime: 15_000,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <SessionProvider>{children}</SessionProvider>
    </QueryClientProvider>
  );
}
