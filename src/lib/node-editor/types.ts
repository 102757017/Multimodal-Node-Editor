// types.ts - Frontend type definitions for the VisionMaster-like node editor.
// Mirrors the backend models including the new fields: trigger_mode,
// dynamic_port_configs, Port.metadata, and input_sources (in properties).

export interface PortMetadata {
  is_dynamic?: boolean;
  dynamic_group?: string;
  dynamic_index?: number;
  [key: string]: unknown;
}

export interface Port {
  id: string;
  name: string;
  display_name?: string | null;
  data_type: string;
  direction: "in" | "out" | "inout";
  preview?: boolean;
  metadata?: PortMetadata;
}

export interface DynamicPortConfig {
  group_name: string;
  prefix: string;
  data_type: string;
  direction: "in" | "out";
  min_count: number;
  max_count: number;
  auto_expand: boolean;
  preview: boolean;
}

export interface PropertyDefinition {
  name: string;
  display_name: string;
  type: string; // int | float | string | bool | color
  default: number | string | boolean | null;
  widget: string; // input | number_input | dropdown | checkbox | color
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options?: { value: number | string; label: string }[];
}

export interface NodeDefinition {
  definition_id: string;
  version: string;
  display_name: string;
  description: string;
  order: number;
  category: string;
  is_source_node: boolean | null;
  measure_time: boolean;
  available?: boolean; // false when backend stubbed the compute (missing deps)
  inputs: Port[];
  outputs: Port[];
  properties: PropertyDefinition[];
  dynamic_port_configs: Record<string, DynamicPortConfig>;
}

export interface CategoryDefinition {
  category_id: string;
  display_name: string;
  order: number;
}

/** Hierarchical category tree returned by /api/nodes. */
export interface CategoryTreeNode {
  id: string; // e.g. "audio.analysis"
  display_name: string;
  order: number;
  default_open: boolean;
  children: CategoryTreeNode[];
}

export interface GraphNode {
  id: string;
  definition_id: string;
  definition_version: string | null;
  name: string;
  inputs: Port[];
  outputs: Port[];
  properties: Record<string, unknown>;
  position: { x: number; y: number };
  trigger_mode: "ALL" | "ANY";
  dynamic_port_configs: Record<string, DynamicPortConfig>;
  is_source_node: boolean | null;
}

export interface GraphConnection {
  id: string;
  from_node_id: string;
  from_port_id: string;
  to_node_id: string;
  to_port_id: string;
}

export interface GraphData {
  id: string;
  graph_format_version: string;
  nodes: GraphNode[];
  connections: GraphConnection[];
}

export interface ComboBoxCandidate {
  node_id: string;
  node_name: string;
  port_id: string;
  port_name: string;
  display_name: string;
  data_type: string;
  label: string;
}

export type ExecutionStatus =
  | "running"
  | "frame_complete"
  | "idle"
  | "exhausted";

export interface ExecutionResult {
  status: ExecutionStatus;
  frame_id: number;
  executed_nodes: string[];
  skipped_nodes: string[];
  waiting_nodes: string[];
  outputs: Record<string, unknown>;
  errors: Record<string, string>;
  node_times: Record<string, { name: string; time: number; order: number }>;
  elapsed_ms: number;
}

export interface GraphStatus {
  frame_id: number;
  source_depleted: Record<string, boolean>;
  all_sources_depleted: boolean;
  sync_timeout: number;
  node_count: number;
  connection_count: number;
}

// Colours per data type (for port handles & badges).
export const TYPE_COLORS: Record<string, string> = {
  float: "#10b981", // emerald
  int: "#22c55e", // green
  string: "#f59e0b", // amber
  bool: "#a855f7", // purple
  image: "#ec4899", // pink
  audio: "#06b6d4", // cyan
  any: "#64748b", // slate
};

export function typeColor(t: string | undefined): string {
  if (!t) return TYPE_COLORS.any;
  return TYPE_COLORS[t] || TYPE_COLORS.any;
}

// ---------------------------------------------------------------------------
// Global model registry (shared model instances across nodes)
// ---------------------------------------------------------------------------
export interface ModelRegistryEntry {
  key: string;
  label: string;
  loaded: boolean;
  error: string | null;
  loaded_at: number;
  last_used_at: number;
  load_count: number;
  hit_count: number;
  est_bytes: number;
  est_mb: number;
}

export interface ModelRegistrySnapshot {
  entries: ModelRegistryEntry[];
  total_bytes: number;
  total_mb: number;
  max_bytes: number;
  max_mb: number;
  max_entries: number;
  entry_count: number;
}
