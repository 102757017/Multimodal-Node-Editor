"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Trash2, RefreshCw, Database, Cpu, AlertTriangle, CheckCircle2, Plus } from "lucide-react";
import { api } from "@/lib/node-editor/api";
import type { ModelRegistrySnapshot } from "@/lib/node-editor/types";

interface ModelsPanelProps {
  /** When true, polls the registry on an interval. */
  active: boolean;
}

export function ModelsPanel({ active }: ModelsPanelProps) {
  const [snapshot, setSnapshot] = useState<ModelRegistrySnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [showPreload, setShowPreload] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await api.listModels();
      setSnapshot(s);
    } catch {
      /* ignore */
    }
  }, []);

  // initial load + polling when active
  useEffect(() => {
    refresh();
    if (!active) return;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [refresh, active]);

  const handleUnload = useCallback(async (key: string) => {
    setLoading(true);
    try {
      const r = await api.unloadModel(key);
      setSnapshot(r.snapshot);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleUnloadAll = useCallback(async () => {
    if (!snapshot || snapshot.entry_count === 0) return;
    if (!confirm(`Unload all ${snapshot.entry_count} models? This frees ${snapshot.total_mb.toFixed(1)} MB.`)) return;
    setLoading(true);
    try {
      const r = await api.unloadAllModels();
      setSnapshot(r.snapshot);
    } finally {
      setLoading(false);
    }
  }, [snapshot]);

  const entries = snapshot?.entries || [];
  const totalMb = snapshot?.total_mb || 0;
  const maxMb = snapshot?.max_mb || 0;
  const pct = maxMb > 0 ? Math.min(100, (totalMb / maxMb) * 100) : 0;

  return (
    <div className="h-full flex flex-col">
      {/* header */}
      <div className="p-3 border-b border-zinc-800 space-y-2">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-cyan-400" />
          <span className="text-xs font-semibold text-zinc-200">Model Registry</span>
          <Badge variant="outline" className="text-[9px] ml-auto border-zinc-600 text-zinc-300 bg-zinc-800">
            {snapshot?.entry_count || 0} / {snapshot?.max_entries || 0}
          </Badge>
        </div>
        <p className="text-[10px] text-zinc-500 leading-relaxed">
          Shared model instances across all nodes. Same model = one copy in memory.
        </p>
        {/* memory bar */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[10px] text-zinc-400">
            <span>Memory</span>
            <span className="font-mono">{totalMb.toFixed(1)} / {maxMb.toFixed(0)} MB</span>
          </div>
          <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
            <div
              className={`h-full transition-all ${pct > 80 ? "bg-red-500" : pct > 50 ? "bg-amber-500" : "bg-cyan-500"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        {/* actions */}
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-[10px] gap-1 px-2"
            onClick={refresh}
            disabled={loading}
          >
            <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} /> Refresh
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-[10px] gap-1 px-2 text-cyan-300 hover:bg-cyan-950/40"
            onClick={() => setShowPreload(true)}
            disabled={loading}
          >
            <Plus className="w-3 h-3" /> Preload
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-[10px] gap-1 px-2 text-red-400 hover:text-red-300 hover:bg-red-950/40"
            onClick={handleUnloadAll}
            disabled={loading || entries.length === 0}
          >
            <Trash2 className="w-3 h-3" /> Unload All
          </Button>
        </div>
      </div>

      {/* model list */}
      <ScrollArea className="flex-1 overflow-x-hidden">
        <div className="p-2 space-y-1.5 w-full max-w-full overflow-x-hidden box-border">
          {entries.length === 0 && (
            <div className="text-center text-[11px] text-zinc-600 py-8">
              <Cpu className="w-5 h-5 mx-auto mb-2 opacity-40" />
              No models loaded yet.
              <br />
              Run a node that uses a model to see it here.
            </div>
          )}
          {entries.map((e) => (
            <div
              key={e.key}
              className="rounded-md border border-zinc-800 bg-zinc-900/60 p-2 space-y-1.5"
            >
              <div className="flex items-start gap-1.5">
                {e.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 mt-0.5 shrink-0" />
                ) : e.error ? (
                  <AlertTriangle className="w-3.5 h-3.5 text-red-400 mt-0.5 shrink-0" />
                ) : (
                  <div className="w-3.5 h-3.5 rounded-full border-2 border-zinc-600 mt-0.5 shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] font-medium text-zinc-200 truncate" title={e.key}>
                    {e.label || "model"}
                  </div>
                  <div className="text-[9px] text-zinc-500 font-mono truncate" title={e.key}>
                    {e.key}
                  </div>
                </div>
                {e.loaded && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 w-6 p-0 text-red-400 hover:bg-red-950/40"
                    onClick={() => handleUnload(e.key)}
                    disabled={loading}
                    title="Unload this model"
                  >
                    <Trash2 className="w-3 h-3" />
                  </Button>
                )}
              </div>
              {/* stats row */}
              <div className="flex items-center gap-2 text-[9px] text-zinc-500">
                {e.loaded && (
                  <Badge className="text-[8px] h-4 px-1 bg-cyan-600/20 text-cyan-300 border-cyan-600/40">
                    {e.est_mb.toFixed(1)} MB
                  </Badge>
                )}
                <span title="Times the loader was actually called">loads: {e.load_count}</span>
                <span title="Times a cached instance was returned (no reload)">hits: {e.hit_count}</span>
              </div>
              {e.error && (
                <div className="text-[9px] text-red-400 break-words bg-red-950/30 rounded p-1">
                  {e.error}
                </div>
              )}
              {e.loaded && e.last_used_at > 0 && (
                <div className="text-[8px] text-zinc-600">
                  last used {timeAgo(e.last_used_at)}
                </div>
              )}
            </div>
          ))}
        </div>
      </ScrollArea>
      {showPreload && (
        <PreloadDialog
          onClose={() => setShowPreload(false)}
          onLoaded={(s) => { setSnapshot(s); setShowPreload(false); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PreloadDialog — pick a registered loader + key, preload into the registry
// ---------------------------------------------------------------------------
function PreloadDialog({
  onClose,
  onLoaded,
}: {
  onClose: () => void;
  onLoaded: (s: ModelRegistrySnapshot) => void;
}) {
  const [loaders, setLoaders] = useState<string[]>([]);
  const [loaderName, setLoaderName] = useState("");
  const [key, setKey] = useState("");
  const [argsJson, setArgsJson] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listModelLoaders().then((r) => {
      const names = r.loaders.map((l) => l.name);
      setLoaders(names);
      if (names.length > 0 && !loaderName) setLoaderName(names[0]);
    }).catch(() => {});
  }, [loaderName]);

  const handlePreload = async () => {
    setBusy(true);
    setError(null);
    try {
      let args: Record<string, unknown> = {};
      try { args = JSON.parse(argsJson || "{}"); } catch { throw new Error("Args must be valid JSON"); }
      const finalKey = key || `${loaderName}:${Date.now()}`;
      const r = await api.preloadModel(finalKey, loaderName, args, loaderName);
      onLoaded(r.snapshot);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="absolute inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-lg p-4 w-full max-w-sm space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm font-semibold text-zinc-100">Preload Model</div>
        <p className="text-[10px] text-zinc-500">
          Select a registered loader and provide a cache key.  The model will be
          loaded once and shared by all nodes that use the same key.
        </p>
        {loaders.length === 0 ? (
          <div className="text-[11px] text-amber-400 bg-amber-950/30 rounded p-2">
            No model loaders registered.  Nodes that use the global registry
            register their loaders automatically — add a node that uses a model
            and run it once first.
          </div>
        ) : (
          <>
            <div className="space-y-1">
              <label className="text-[10px] text-zinc-400">Loader</label>
              <select
                value={loaderName}
                onChange={(e) => setLoaderName(e.target.value)}
                className="w-full h-8 text-xs bg-zinc-800 border border-zinc-700 rounded px-2 text-zinc-200"
              >
                {loaders.map((l) => (
                  <option key={l} value={l}>{l}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-zinc-400">Cache key (optional)</label>
              <input
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={`${loaderName}:default`}
                className="w-full h-8 text-xs bg-zinc-800 border border-zinc-700 rounded px-2 text-zinc-200 font-mono"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-zinc-400">Loader args (JSON)</label>
              <textarea
                value={argsJson}
                onChange={(e) => setArgsJson(e.target.value)}
                rows={3}
                className="w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono"
              />
            </div>
          </>
        )}
        {error && <div className="text-[10px] text-red-400 break-words">{error}</div>}
        <div className="flex gap-2 justify-end">
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={onClose}>Cancel</Button>
          <Button
            size="sm"
            className="h-8 text-xs bg-cyan-600 hover:bg-cyan-500"
            disabled={busy || loaders.length === 0 || !loaderName}
            onClick={handlePreload}
          >
            {busy ? "Loading…" : "Preload"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function timeAgo(ts: number): string {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
