"use client";

import { useMemo, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Search, ChevronRight, ChevronDown, AlertTriangle } from "lucide-react";
import { Input } from "@/components/ui/input";
import type { CategoryTreeNode, NodeDefinition } from "@/lib/node-editor/types";

interface NodePaletteProps {
  definitions: NodeDefinition[];
  categories: CategoryTreeNode[];
  onAdd: (def: NodeDefinition) => void;
}

export function NodePalette({ definitions, categories, onAdd }: NodePaletteProps) {
  const [q, setQ] = useState("");
  // null = not yet toggled by user; derive initial state from default_open
  const [expanded, setExpanded] = useState<Set<string> | null>(null);

  const searching = q.trim().length > 0;

  // compute the set of all category ids (for search mode)
  const allIds = useMemo(() => {
    const s = new Set<string>();
    const walk = (nodes: CategoryTreeNode[]) => {
      for (const n of nodes) {
        s.add(n.id);
        walk(n.children);
      }
    };
    walk(categories);
    return s;
  }, [categories]);

  // the effective expanded set: user toggles override; otherwise default_open
  const effectiveExpanded = useMemo(() => {
    if (searching) return allIds; // expand everything while searching
    if (expanded) return expanded;
    // derive from default_open
    const next = new Set<string>();
    for (const cat of categories) {
      if (cat.default_open) next.add(cat.id);
      for (const child of cat.children) {
        if (child.default_open) next.add(child.id);
      }
    }
    return next;
  }, [expanded, searching, allIds, categories]);

  const defsByCategory = useMemo(() => {
    const m = new Map<string, NodeDefinition[]>();
    for (const d of definitions) {
      const arr = m.get(d.category) || [];
      arr.push(d);
      m.set(d.category, arr);
    }
    return m;
  }, [definitions]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const base = prev || effectiveExpanded;
      const next = new Set(base);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function collapseAll() {
    setExpanded(new Set());
  }

  // count nodes under a category (recursively)
  function countNodes(cat: CategoryTreeNode): number {
    const direct = (defsByCategory.get(cat.id) || []).length;
    return direct + cat.children.reduce((sum, c) => sum + countNodes(c), 0);
  }

  // filter nodes by search query
  function matchesSearch(d: NodeDefinition): boolean {
    if (!searching) return true;
    const ql = q.toLowerCase();
    return (
      d.display_name.toLowerCase().includes(ql) ||
      d.definition_id.toLowerCase().includes(ql) ||
      d.description.toLowerCase().includes(ql)
    );
  }

  // render a category node recursively
  function renderCategory(cat: CategoryTreeNode, depth: number): React.ReactNode {
    const nodes = (defsByCategory.get(cat.id) || []).filter(matchesSearch);
    const childMatches = cat.children.map((c) => renderCategory(c, depth + 1)).filter(Boolean);
    const isExpanded = effectiveExpanded.has(cat.id);
    const total = countNodes(cat);
    const visibleCount = nodes.length + cat.children.reduce(
      (s, c) => countInSearch(c),
      0,
    );

    if (searching && visibleCount === 0) return null;
    if (total === 0 && !searching) return null;

    const Chevron = isExpanded ? ChevronDown : ChevronRight;

    return (
      <div key={cat.id}>
        <button
          onClick={() => toggle(cat.id)}
          className="w-full flex items-center gap-1.5 px-1 py-1 text-left hover:bg-zinc-800/50 rounded transition-colors"
          style={{ paddingLeft: 4 + depth * 12 }}
        >
          <Chevron className="w-3 h-3 text-zinc-500 shrink-0" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-300 truncate flex-1">
            {cat.display_name}
          </span>
          <span className="text-[9px] text-zinc-500 tabular-nums">{total}</span>
        </button>
        {isExpanded && (
          <div>
            {nodes.map((d) => renderNodeButton(d, depth + 1))}
            {childMatches}
          </div>
        )}
      </div>
    );
  }

  function countInSearch(cat: CategoryTreeNode): number {
    const nodes = (defsByCategory.get(cat.id) || []).filter(matchesSearch).length;
    return nodes + cat.children.reduce((s, c) => s + countInSearch(c), 0);
  }

  function renderNodeButton(d: NodeDefinition, depth: number): React.ReactNode {
    if (!matchesSearch(d)) return null;
    return (
      <button
        key={d.definition_id}
        onClick={() => onAdd(d)}
        className="block w-full text-left rounded-md border border-zinc-700 bg-zinc-900/60 hover:border-cyan-500 hover:bg-zinc-800/80 transition-colors py-2 pr-2 group min-w-0 box-border"
        style={{ paddingLeft: 8 + depth * 12 }}
        title={d.available === false ? `${d.description} (unavailable — missing dependencies)` : d.description}
      >
        <div className="flex items-center gap-1.5">
          <span className={`text-xs font-medium truncate flex-1 ${d.available === false ? "text-zinc-500" : "text-zinc-200 group-hover:text-cyan-300"}`}>
            {d.display_name}
          </span>
          {d.is_source_node && (
            <Badge className="text-[8px] h-4 px-1 bg-emerald-600/20 text-emerald-300 border-emerald-600/40">
              src
            </Badge>
          )}
          {d.available === false && (
            <AlertTriangle className="w-3 h-3 text-amber-500/70" />
          )}
          {Object.keys(d.dynamic_port_configs).length > 0 && (
            <Badge className="text-[8px] h-4 px-1 bg-cyan-600/20 text-cyan-300 border-cyan-600/40">
              dyn
            </Badge>
          )}
        </div>
        {d.description && searching && (
          <div className="text-[10px] text-zinc-500 mt-0.5 line-clamp-1">
            {d.description}
          </div>
        )}
      </button>
    );
  }

  // flat list of search results (when searching, still show within categories)
  const totalNodes = definitions.length;

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-zinc-800">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
          <Input
            placeholder={`Search ${totalNodes} nodes…`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="h-8 pl-8 text-xs bg-zinc-900 border-zinc-700"
          />
        </div>
        <div className="mt-2 flex items-center gap-2 text-[10px] text-zinc-500">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-500" /> source
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-cyan-500" /> dynamic
          </span>
          <span className="flex items-center gap-1">
            <AlertTriangle className="w-2.5 h-2.5 text-amber-500/70" /> N/A
          </span>
          <button
            className="ml-auto hover:text-zinc-300"
            onClick={collapseAll}
            title="Collapse all"
          >
            collapse
          </button>
        </div>
      </div>
      <ScrollArea className="flex-1 overflow-x-hidden">
        <div className="p-2 space-y-0.5 w-full max-w-full overflow-x-hidden box-border">
          {categories.length === 0 && (
            <div className="text-center text-xs text-zinc-500 py-8">Loading nodes…</div>
          )}
          {categories.map((cat) => renderCategory(cat, 0))}
        </div>
      </ScrollArea>
    </div>
  );
}
