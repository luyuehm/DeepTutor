"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchOpsDashboard, type DashboardRange } from "./api";
import { parseOpsDashboard, type OpsDashboard } from "./model";

interface UseOpsDashboardResult {
  data: OpsDashboard | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useOpsDashboard(range: DashboardRange): UseOpsDashboardResult {
  const [data, setData] = useState<OpsDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await fetchOpsDashboard(range, controller.signal);
        if (!cancelled) {
          setData(parseOpsDashboard(payload));
        }
      } catch (e) {
        if (!cancelled && !controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "Failed to load dashboard");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [range, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  return { data, loading, error, refresh };
}
