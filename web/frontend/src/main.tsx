import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles.css";

// retry: false — a failed read is SHOWN as stale, not silently retried behind the
// operator's back. The 2 s poll is the retry.
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 0, gcTime: 60_000 },
    mutations: { retry: false },
  },
});

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root is missing from index.html");
}
createRoot(container).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);