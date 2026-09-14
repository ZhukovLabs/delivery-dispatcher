"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export default function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: "always",
      },
    },
  }));
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
