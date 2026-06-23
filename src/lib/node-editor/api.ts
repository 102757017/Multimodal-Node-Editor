// api.ts - Client for the node-editor FastAPI backend.
// All requests go through the Caddy gateway using ?XTransformPort=3030.

import type {
  ComboBoxCandidate,
  ExecutionResult,
  GraphData,
  GraphNode,
  GraphStatus,
  ModelRegistrySnapshot,
  NodeDefinition,
  Port,
  CategoryTreeNode,
} from "./types";

const PORT = 3030;

function url(path: string): string {
  return `${path}?XTransformPort=${PORT}`;
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(url(path));
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function jpost<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(url(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function jput<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(url(path), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function jdel<T>(path: string): Promise<T> {
  const r = await fetch(url(path), { method: "DELETE" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

// ---------------------------------------------------------------------------

export interface NodeListResponse {
  nodes: NodeDefinition[];
  categories: CategoryTreeNode[];
}

export const api = {
  // node definitions
  listNodes: () => jget<NodeListResponse>("/api/nodes"),

  // graph
  getGraph: () => jget<GraphData>("/api/graph"),
  addNode: (definition_id: string, position?: { x: number; y: number }, name?: string) =>
    jpost<GraphNode>("/api/graph/nodes", { definition_id, position, name }),
  removeNode: (id: string) => jdel<{ ok: boolean }>(`/api/graph/nodes/${id}`),

  addConnection: (from_node_id: string, from_port_id: string, to_node_id: string, to_port_id: string) =>
    jpost<{ id: string } | { detail: string }>("/api/graph/connections", {
      from_node_id, from_port_id, to_node_id, to_port_id,
    }),
  removeConnection: (id: string) => jdel<{ ok: boolean }>(`/api/graph/connections/${id}`),

  // node config
  setProperties: (nodeId: string, properties: Record<string, unknown>) =>
    jput<{ ok: boolean }>(`/api/graph/nodes/${nodeId}/properties`, { properties }),
  updateNodePosition: (nodeId: string, position: { x: number; y: number }) =>
    jput<{ ok: boolean }>(`/api/graph/nodes/${nodeId}/position`, { position }),
  autoLayout: (opts?: {
    direction?: "LR" | "TB";
    node_width?: number;
    node_height?: number;
    layer_gap?: number;
    node_gap?: number;
  }) =>
    jpost<{ ok: boolean; positions: Record<string, { x: number; y: number }> }>(
      "/api/graph/auto-layout",
      opts || {},
    ),
  setTriggerMode: (nodeId: string, mode: "ALL" | "ANY") =>
    jput<{ ok: boolean }>(`/api/graph/nodes/${nodeId}/trigger-mode`, { mode }),
  setInputSource: (nodeId: string, port_name: string, source: string | null) =>
    jput<{ ok: boolean }>(`/api/graph/nodes/${nodeId}/input-source`, { port_name, source }),
  getComboboxCandidates: (nodeId: string, portName: string) =>
    jget<{ candidates: ComboBoxCandidate[] }>(`/api/graph/nodes/${nodeId}/combobox/${encodeURIComponent(portName)}`),

  // dynamic ports
  addDynamicPort: (nodeId: string, group_name: string) =>
    jpost<Port>(`/api/graph/nodes/${nodeId}/dynamic-port`, { group_name }),
  removeDynamicPort: (nodeId: string, portId: string) =>
    jdel<GraphNode>(`/api/graph/nodes/${nodeId}/dynamic-port/${portId}`),
  renamePort: (nodeId: string, portId: string, display_name: string) =>
    jput<{ ok: boolean }>(`/api/graph/nodes/${nodeId}/port/${portId}/rename`, { display_name }),

  // execution
  startFrame: () => jpost<{ ok: boolean; frame_id: number }>("/api/graph/start-frame"),
  pushSourceData: (nodeId: string, data: Record<string, unknown>) =>
    jpost<{ ok: boolean; frame_id: number }>(`/api/graph/source-data/${nodeId}`, { data }),
  markDepleted: (nodeId: string) =>
    jpost<{ ok: boolean }>(`/api/graph/mark-depleted/${nodeId}`),
  executeStep: (context?: Record<string, unknown>) =>
    jpost<ExecutionResult>("/api/graph/execute-step", { context }),
  resetFrame: () => jpost<{ ok: boolean }>("/api/graph/reset-frame"),
  getStatus: () => jget<GraphStatus>("/api/graph/status"),

  // save / load
  saveGraph: (path?: string) => jpost<{ ok: boolean; path: string }>("/api/graph/save", { path }),
  loadGraph: (data?: GraphData) => jpost<{ ok: boolean; graph: GraphData }>("/api/graph/load", { data }),

  // file upload (for file_picker properties — uploads to backend, returns path)
  uploadFile: async (file: File, onProgress?: (pct: number) => void): Promise<{ path: string; first_frame?: string; frame_count?: number }> => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); }
          catch { reject(new Error("Invalid response")); }
        } else { reject(new Error(`Upload failed: ${xhr.status}`)); }
      };
      xhr.onerror = () => reject(new Error("Network error"));
      xhr.onabort = () => reject(new Error("Upload cancelled"));
      xhr.timeout = 600000;
      xhr.ontimeout = () => reject(new Error("Upload timeout"));
      const fd = new FormData();
      fd.append("file", file);
      xhr.open("POST", `/api/upload?XTransformPort=${PORT}`);
      xhr.send(fd);
    });
  },

  // model registry
  listModels: () => jget<ModelRegistrySnapshot>("/api/models"),
  unloadModel: (key: string) => jdel<{ ok: boolean; snapshot: ModelRegistrySnapshot }>(`/api/models/${encodeURIComponent(key)}`),
  unloadAllModels: () => jdel<{ ok: boolean; snapshot: ModelRegistrySnapshot }>("/api/models"),
  listModelLoaders: () => jget<{ loaders: { name: string }[] }>("/api/model-loaders"),
  preloadModel: (key: string, loader_name: string, loader_args: Record<string, unknown>, label?: string, est_bytes?: number) =>
    jpost<{ ok: boolean; snapshot: ModelRegistrySnapshot }>("/api/models/preload", {
      key, loader_name, loader_args, label, est_bytes,
    }),
};
