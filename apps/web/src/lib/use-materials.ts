"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getProjectMaterial,
  listProjectMaterials,
  type MaterialDetailResponse,
  type MaterialFilters,
  type MaterialSummary,
} from "@/lib/d18/material-client";

export interface MaterialLibraryState {
  filters: MaterialFilters;
  selectedId: string | null;
  expandedIds: ReadonlySet<string>;
  inFlight: ReadonlySet<string>;
}

export function useMaterials(researchId: string, fetcher: typeof fetch = globalThis.fetch) {
  const [filters, setFilters] = useState<MaterialFilters>({ deleted: "false" });
  const [materials, setMaterials] = useState<MaterialSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MaterialDetailResponse | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [inFlight, setInFlight] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const requestSequence = useRef(0);
  const backgroundInFlight = useRef(false);

  const runRefresh = useCallback(async (background: boolean) => {
    if (background && backgroundInFlight.current) return;
    if (background) backgroundInFlight.current = true;
    const sequence = ++requestSequence.current;
    if (!background) setLoading(true);
    try {
      const response = await listProjectMaterials(researchId, filters, fetcher);
      if (sequence !== requestSequence.current) return;
      setMaterials(response.materials);
      setError(null);
      if (selectedId && !response.materials.some((row) => row.material_id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      setError(cause instanceof Error ? cause.message : "material request failed");
    } finally {
      if (background) backgroundInFlight.current = false;
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [fetcher, filters, researchId, selectedId]);

  const refresh = useCallback(() => runRefresh(false), [runRefresh]);
  const refreshInBackground = useCallback(() => runRefresh(true), [runRefresh]);

  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 0);
    return () => clearTimeout(timer);
  }, [refresh]);

  useEffect(() => {
    const processing = materials.some((row) =>
      ["pending_security", "queued", "inspecting", "parsing"].includes(
        row.current_revision.processing_status,
      ),
    );
    if (!processing) return;
    const timer = setInterval(() => void refreshInBackground(), 1000);
    return () => clearInterval(timer);
  }, [materials, refreshInBackground]);

  useEffect(() => {
    const onChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ researchId?: string }>).detail;
      if (detail?.researchId === researchId) void refreshInBackground();
    };
    window.addEventListener("d18:material-library-changed", onChanged);
    return () => window.removeEventListener("d18:material-library-changed", onChanged);
  }, [refreshInBackground, researchId]);

  const select = useCallback(
    async (materialId: string) => {
      setSelectedId(materialId);
      setInFlight((prior) => new Set(prior).add(`detail:${materialId}`));
      try {
        const response = await getProjectMaterial(researchId, materialId, fetcher);
        setDetail(response);
        setError(null);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "material request failed");
      } finally {
        setInFlight((prior) => {
          const next = new Set(prior);
          next.delete(`detail:${materialId}`);
          return next;
        });
      }
    },
    [fetcher, researchId],
  );

  const command = useCallback(
    async <T,>(key: string, mutation: () => Promise<T>) => {
      setInFlight((prior) => new Set(prior).add(key));
      try {
        const value = await mutation();
        await refresh();
        if (selectedId) await select(selectedId);
        return value;
      } finally {
        setInFlight((prior) => {
          const next = new Set(prior);
          next.delete(key);
          return next;
        });
      }
    },
    [refresh, select, selectedId],
  );

  const toggleExpanded = useCallback((materialId: string) => {
    setExpandedIds((prior) => {
      const next = new Set(prior);
      if (next.has(materialId)) next.delete(materialId);
      else next.add(materialId);
      return next;
    });
  }, []);

  return useMemo(
    () => ({
      materials,
      detail,
      filters,
      setFilters,
      selectedId,
      select,
      expandedIds,
      toggleExpanded,
      inFlight,
      command,
      refresh,
      loading,
      error,
    }),
    [
      command,
      detail,
      error,
      expandedIds,
      filters,
      inFlight,
      loading,
      materials,
      refresh,
      select,
      selectedId,
      toggleExpanded,
    ],
  );
}
