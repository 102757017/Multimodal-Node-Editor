"use client";

import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Trash2, Zap } from "lucide-react";
import { api } from "@/lib/node-editor/api";
import type {
  ComboBoxCandidate,
  GraphNode,
  NodeDefinition,
} from "@/lib/node-editor/types";
import { typeColor } from "@/lib/node-editor/types";
import { Slider } from "@/components/ui/slider";

interface ConfigPanelProps {
  node: GraphNode | null;
  definition: NodeDefinition | null;
  connectedInputPortIds: Set<string>;
  onNodeChanged: () => void;
  onRenameNode?: (id: string, name: string) => void;
}

export function ConfigPanel({
  node,
  definition,
  connectedInputPortIds,
  onNodeChanged,
  onRenameNode,
}: ConfigPanelProps) {
  const [combobox, setCombobox] = useState<Record<string, ComboBoxCandidate[]>>({});
  const [busy, setBusy] = useState(false);

  // refresh combobox candidates for each input port whenever the node or graph changes
  useEffect(() => {
    if (!node) return;
    let cancelled = false;
    (async () => {
      const next: Record<string, ComboBoxCandidate[]> = {};
      for (const p of node.inputs) {
        try {
          const r = await api.getComboboxCandidates(node.id, p.name);
          if (!cancelled) next[p.name] = r.candidates;
        } catch {
          /* ignore */
        }
      }
      if (!cancelled) setCombobox(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [node?.id, node?.inputs.length, connectedInputPortIds.size]);

  // local overrides for property values — keeps text inputs responsive
  // without triggering a full graph refresh on every keystroke.
  const [localProps, setLocalProps] = useState<Record<string, unknown>>({});
  // clear local overrides when switching nodes
  useEffect(() => {
    setLocalProps({});
  }, [node?.id]);

  if (!node) {
    return (
      <div className="h-full flex items-center justify-center p-6 text-center text-sm text-zinc-500">
        Select a node to edit its properties, trigger mode and input sources.
      </div>
    );
  }

  const inputSources = (node.properties.input_sources || {}) as Record<string, string>;

  function getProp(name: string): unknown {
    if (name in localProps) return localProps[name];
    return node?.properties[name];
  }

  async function handlePropertyChange(name: string, value: unknown) {
    if (!node) return;
    // optimistic local update so the input stays focused & responsive
    setLocalProps((p) => ({ ...p, [name]: value }));
  }

  async function commitProperty(name: string, value: unknown) {
    if (!node) return;
    setBusy(true);
    try {
      await api.setProperties(node.id, { [name]: value });
      onNodeChanged();
      // clear the local override once the backend confirms
      setLocalProps((p) => { const n = { ...p }; delete n[name]; return n; });
    } finally {
      setBusy(false);
    }
  }

  async function handleTriggerMode(mode: "ALL" | "ANY") {
    if (!node) return;
    setBusy(true);
    try {
      await api.setTriggerMode(node.id, mode);
      onNodeChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleInputSource(portName: string, source: string | null) {
    if (!node) return;
    setBusy(true);
    try {
      await api.setInputSource(node.id, portName, source);
      onNodeChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleAddDynamicPort(group: string) {
    if (!node) return;
    setBusy(true);
    try {
      await api.addDynamicPort(node.id, group);
      onNodeChanged();
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="h-full overflow-y-auto overflow-x-hidden"
      style={{ userSelect: "text", WebkitUserSelect: "text" }}
    >
      <div className="p-4 space-y-5 w-full max-w-full overflow-x-hidden box-border">
        {/* header */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-[10px] border-zinc-600 text-zinc-300 bg-zinc-800">
              {definition?.category || "general"}
            </Badge>
            {node.is_source_node && (
              <Badge className="text-[10px] bg-emerald-600/20 text-emerald-300 border-emerald-600/40">
                source
              </Badge>
            )}
          </div>
          <div className="space-y-1">
            <Label className="text-[11px] text-zinc-400">Node name</Label>
            <Input
              key={node.id}
              defaultValue={node.name}
              onBlur={(e) => onRenameNode && onRenameNode(node.id, e.target.value)}
              className="h-8 text-sm"
            />
          </div>
          <p className="text-[11px] text-zinc-500 leading-relaxed">
            {definition?.description}
          </p>
        </div>

        {/* trigger mode */}
        <div className="space-y-1.5">
          <Label className="text-[11px] text-zinc-400 flex items-center gap-1">
            <Zap className="w-3 h-3" /> Trigger mode
          </Label>
          <Select value={node.trigger_mode} onValueChange={(v) => handleTriggerMode(v as "ALL" | "ANY")}>
            <SelectTrigger className="h-8 text-xs w-full max-w-full" disabled={busy}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">ALL — execute once when all inputs ready</SelectItem>
              <SelectItem value="ANY">ANY — execute on any input update</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[10px] text-zinc-500">
            {node.trigger_mode === "ALL"
              ? "Frame-synced: waits for every connected input this frame."
              : "Reactive: fires whenever any connected input updates (may run multiple times per frame)."}
          </p>
        </div>

        {/* properties */}
        {definition && definition.properties.length > 0 && (
          <div className="space-y-3">
            <div className="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider">
              Properties
            </div>
            {definition.properties.map((pd) => (
              <PropertyField
                key={pd.name}
                def={pd}
                value={getProp(pd.name)}
                disabled={busy}
                onChange={(v) => handlePropertyChange(pd.name, v)}
                onCommit={(v) => commitProperty(pd.name, v)}
              />
            ))}
          </div>
        )}

        {/* input sources (ComboBox cross-level access) */}
        {node.inputs.length > 0 && (
          <div className="space-y-3">
            <div className="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider">
              Input sources
            </div>
            <p className="text-[10px] text-zinc-500 leading-relaxed">
              Direct connections take priority. When a port is connected, its
              ComboBox is disabled.
            </p>
            {node.inputs.map((p) => {
              const connected = connectedInputPortIds.has(p.id);
              const current = inputSources[p.name];
              return (
                <div key={p.id} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="w-2 h-2 rounded-full"
                      style={{ background: typeColor(p.data_type) }}
                    />
                    <Label className="text-[11px] text-zinc-300 flex-1">
                      {p.display_name || p.name}
                    </Label>
                    <span className="text-[9px] text-zinc-500">{p.data_type}</span>
                  </div>
                  <Select
                    value={connected ? "__connected__" : current || "__none__"}
                    onValueChange={(v) => {
                      if (v === "__none__" || v === "__connected__") {
                        handleInputSource(p.name, null);
                      } else {
                        handleInputSource(p.name, v);
                      }
                    }}
                    disabled={busy || connected}
                  >
                    <SelectTrigger className="h-8 text-xs w-full max-w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">(none — use default)</SelectItem>
                      {connected && (
                        <SelectItem value="__connected__" disabled>
                          (provided by connection)
                        </SelectItem>
                      )}
                      {(combobox[p.name] || []).map((c) => (
                        <SelectItem key={c.node_id + c.port_id} value={`${c.node_id}.${c.port_name}`}>
                          {c.label}
                        </SelectItem>
                      ))}
                      {!connected && (combobox[p.name] || []).length === 0 && (
                        <SelectItem value="__empty__" disabled>
                          (connect a wire to populate upstream outputs)
                        </SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </div>
              );
            })}
          </div>
        )}

        {/* dynamic port groups */}
        {Object.keys(node.dynamic_port_configs).length > 0 && (
          <div className="space-y-3">
            <div className="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider">
              Dynamic port groups
            </div>
            {Object.entries(node.dynamic_port_configs).map(([group, cfg]) => {
              const count = node.inputs.filter(
                (p) => p.metadata?.dynamic_group === group,
              ).length + node.outputs.filter(
                (p) => p.metadata?.dynamic_group === group,
              ).length;
              return (
                <div
                  key={group}
                  className="rounded-md border border-zinc-700/60 p-2.5 space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-xs font-medium text-zinc-200">{group}</div>
                      <div className="text-[10px] text-zinc-500">
                        {cfg.prefix} · {cfg.data_type} · {count}/{cfg.max_count}
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-[11px] gap-1"
                      disabled={busy || count >= cfg.max_count}
                      onClick={() => handleAddDynamicPort(group)}
                    >
                      <Plus className="w-3 h-3" /> Add
                    </Button>
                  </div>
                  <div className="text-[10px] text-zinc-500">
                    Hover a dynamic port on the canvas to delete it (unconnected
                    only; min {cfg.min_count}).
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* delete node */}
        <div className="pt-2 border-t border-zinc-800">
          <Button
            size="sm"
            variant="ghost"
            className="w-full text-red-400 hover:text-red-300 hover:bg-red-950/40"
            disabled={busy}
            onClick={() => {
              if (node && confirm(`Delete node "${node.name}"?`)) {
                api.removeNode(node.id).then(onNodeChanged);
              }
            }}
          >
            <Trash2 className="w-3.5 h-3.5 mr-1.5" /> Delete node
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Property field renderer
// ---------------------------------------------------------------------------
function PropertyField({
  def,
  value,
  disabled,
  onChange,
  onCommit,
}: {
  def: NodeDefinition["properties"][number];
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
  onCommit?: (v: unknown) => void | Promise<void>;
}) {
  // coerce initial value
  const v = value === undefined || value === null ? def.default : value;

  // ---- clamp helper for numeric values ----
  const clamp = (n: number): number => {
    let r = n;
    if (def.min !== undefined && def.min !== null) r = Math.max(def.min, r);
    if (def.max !== undefined && def.max !== null) r = Math.min(def.max, r);
    return r;
  };

  // ---- slider widget (for float/int with min & max) ----
  if (def.widget === "slider" && def.min !== undefined && def.max !== undefined) {
    const numVal = typeof v === "number" ? v : parseFloat(String(v)) || def.min || 0;
    const clamped = clamp(numVal);
    const step = def.step ?? (def.type === "float" ? 0.01 : 1);
    return (
      <div className="space-y-1.5 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <Label className="text-[11px] text-zinc-400 truncate">{def.display_name}</Label>
          <span className="text-[10px] text-zinc-300 font-mono tabular-nums shrink-0">
            {def.type === "int" ? Math.round(clamped) : clamped.toFixed(2)}
          </span>
        </div>
        <Slider
          value={[clamped]}
          min={def.min}
          max={def.max}
          step={step}
          onValueChange={(vals) => {
            const nv = vals[0];
            const final = def.type === "int" ? Math.round(nv) : nv;
            onChange(final);
            onCommit?.(final);
          }}
          disabled={disabled}
          className="w-full"
        />
      </div>
    );
  }

  if (def.widget === "dropdown" && def.options && def.options.length > 0) {
    return (
      <div className="space-y-1 min-w-0">
        <Label className="text-[11px] text-zinc-400">{def.display_name}</Label>
        <Select
          value={String(v ?? "")}
          onValueChange={(val) => { const fv = def.type === "int" ? parseInt(val, 10) : val; onChange(fv); onCommit?.(fv); }}
          disabled={disabled}
        >
          <SelectTrigger className="h-8 text-xs w-full max-w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {def.options.map((o) => (
              <SelectItem key={String(o.value)} value={String(o.value)}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }

  if (def.widget === "checkbox" || def.type === "bool") {
    return (
      <div className="flex items-center justify-between">
        <Label className="text-[11px] text-zinc-400 truncate">{def.display_name}</Label>
        <input
          type="checkbox"
          checked={Boolean(v)}
          disabled={disabled}
          onChange={(e) => { onChange(e.target.checked); onCommit?.(e.target.checked); }}
          className="w-4 h-4 accent-cyan-500 shrink-0"
        />
      </div>
    );
  }

  if (def.widget === "color") {
    return (
      <div className="space-y-1 min-w-0">
        <Label className="text-[11px] text-zinc-400">{def.display_name}</Label>
        <div className="flex gap-2 min-w-0">
          <input
            type="color"
            value={String(v || "#000000")}
            disabled={disabled}
            onChange={(e) => { onChange(e.target.value); onCommit?.(e.target.value); }}
            className="w-9 h-8 rounded border border-zinc-700 bg-transparent cursor-pointer shrink-0"
          />
          <Input
            value={String(v || "")}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
            onBlur={() => onCommit?.(v)}
            className="h-8 text-xs flex-1 min-w-0"
          />
        </div>
      </div>
    );
  }

  if (def.widget === "file_picker") {
    // file picker: shows a text input + a "Browse…" button that opens a file
    // dialog.  The selected file is uploaded to the backend which stores it
    // locally and returns the path; the path is then saved as the property.
    return (
      <FilePickerField
        def={def}
        value={v}
        disabled={disabled}
        onChange={onChange}
        onCommit={onCommit}
      />
    );
  }

  // number / text input
  const isNumber = def.type === "int" || def.type === "float";
  return (
    <div className="space-y-1 min-w-0">
      <Label className="text-[11px] text-zinc-400">{def.display_name}</Label>
      <Input
        type={isNumber ? "number" : "text"}
        value={v === undefined || v === null ? "" : String(v)}
        disabled={disabled}
        step={def.step ?? (def.type === "float" ? 0.1 : 1)}
        min={def.min ?? undefined}
        max={def.max ?? undefined}
        onChange={(e) => {
          const raw = e.target.value;
          if (isNumber) {
            if (raw === "") { onChange(0); return; }
            let num = def.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
            if (isNaN(num)) num = 0;
            onChange(clamp(num));
          } else {
            onChange(raw);
          }
        }}
        onBlur={() => {
          // commit to backend on blur (so typing stays responsive)
          if (isNumber && typeof v === "number") { const c = clamp(v); onChange(c); onCommit?.(c); }
          else { onCommit?.(v); }
        }}
        className="h-8 text-xs w-full"
      />
      {isNumber && def.min !== undefined && def.max !== undefined && (
        <div className="text-[9px] text-zinc-600">
          range: {def.min} ~ {def.max}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FilePickerField — matches the original project's FilePickerWidget.
//
// Browsers cannot expose a file's real local path for security, so (like the
// original project) we upload the file to the backend which stores it and
// returns the absolute storage path.  That path is saved as the property
// value; nodes read the file from it.  The upload also returns a preview
// image (first frame for videos, the image itself for images) which we
// display immediately.
// ---------------------------------------------------------------------------
function FilePickerField({
  def,
  value,
  disabled,
  onChange,
  onCommit,
}: {
  def: NodeDefinition["properties"][number];
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
  onCommit?: (v: unknown) => void | Promise<void>;
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [preview, setPreview] = useState<string | null>(null);

  const cancelUpload = () => {
    if (xhrRef.current) {
      xhrRef.current.abort();
      xhrRef.current = null;
    }
    setUploading(false);
    setProgress(0);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleBrowse = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setProgress(-1); // indeterminate
    try {
      const r = await api.uploadFile(file, (pct) => setProgress(pct));
      onChange(r.path);
      // Persist the file_path to the backend immediately so that the
      // node's compute() can read the file when the user clicks Run.
      // Without this, the path only lives in localProps (frontend state)
      // and the backend never receives it — the Image node returns None.
      // We AWAIT onCommit to guarantee the backend has the path before
      // the upload spinner disappears and the user can click Run.
      if (onCommit) await onCommit(r.path);
      if (r.first_frame) setPreview(r.first_frame);
    } catch (err) {
      if (err instanceof Error && err.message !== "Upload cancelled") {
        console.error("upload failed:", err);
      }
    } finally {
      setUploading(false);
      setProgress(0);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const accept = def.accept || undefined;
  // display just the filename from the stored path
  const displayPath = String(value || "");
  const fileName = displayPath ? displayPath.split(/[/\\]/).pop() : "";

  return (
    <div className="space-y-1 min-w-0">
      <Label className="text-[11px] text-zinc-400">{def.display_name}</Label>
      <div className="flex gap-1.5 min-w-0 items-center">
        <input
          ref={fileInputRef}
          type="file"
          accept={accept}
          onChange={handleFileChange}
          className="hidden"
          disabled={disabled || uploading}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 px-2 shrink-0 border-zinc-600 text-zinc-300 hover:bg-zinc-700"
          disabled={disabled || uploading}
          onClick={handleBrowse}
          title="Open file…"
        >
          {uploading ? "…" : "Open"}
        </Button>
        <span
          className="text-[10px] text-zinc-400 truncate flex-1 min-w-0 font-mono"
          title={displayPath}
        >
          {fileName || "No file"}
        </span>
        {uploading && (
          <button
            onClick={cancelUpload}
            className="text-[9px] text-red-400 hover:text-red-300 shrink-0"
            title="Cancel upload"
          >
            ✕
          </button>
        )}
      </div>
      {/* progress bar */}
      {uploading && (
        <div className="h-1 rounded-full bg-zinc-800 overflow-hidden">
          <div
            className="h-full bg-cyan-500 transition-all"
            style={{ width: progress < 0 ? "40%" : `${progress}%` }}
          />
        </div>
      )}
      {/* preview thumbnail */}
      {preview && !uploading && (
        <img
          src={preview}
          alt="preview"
          className="mt-1 rounded max-w-full max-h-32 object-contain border border-zinc-700"
        />
      )}
      {accept && !uploading && !preview && (
        <div className="text-[9px] text-zinc-600">accept: {accept}</div>
      )}
    </div>
  );
}
