"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node as RFNode,
  type NodeChange,
  type EdgeChange,
  MarkerType,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { NodePalette } from "@/components/node-editor/NodePalette";
import { ConfigPanel } from "@/components/node-editor/ConfigPanel";
import { ModelsPanel } from "@/components/node-editor/ModelsPanel";
import { CustomNode, type CustomNodeData } from "@/components/node-editor/CustomNode";
import { api, type NodeListResponse } from "@/lib/node-editor/api";
import type {
  CategoryTreeNode,
  ExecutionResult,
  ExecutionStatus,
  GraphData,
  GraphNode,
  GraphStatus,
  NodeDefinition,
  Port,
} from "@/lib/node-editor/types";
import { typeColor } from "@/lib/node-editor/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Play, Square, SkipForward, RotateCcw, Save, FolderOpen, Activity, LayoutGrid } from "lucide-react";

const nodeTypes = { custom: CustomNode };

function EditorInner() {
  const [definitions, setDefinitions] = useState<NodeDefinition[]>([]);
  const [categories, setCategories] = useState<CategoryTreeNode[]>([]);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<CustomNodeData>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [status, setStatus] = useState<GraphStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"config" | "models">("config");
  const [contextMenu, setContextMenu] = useState<{ nodeId: string; x: number; y: number } | null>(null);
  const [commentModal, setCommentModal] = useState<{ nodeId: string; value: string } | null>(null);
  const [edgeContextMenu, setEdgeContextMenu] = useState<{ edgeId: string; x: number; y: number } | null>(null);
  const runningRef = useRef(false);
  // ref mirror of rfNodes positions so syncFromGraph doesn't depend on rfNodes
  const positionsRef = useRef<Map<string, { x: number; y: number }>>(new Map());

  const defMap = useMemo(() => {
    const m = new Map<string, NodeDefinition>();
    for (const d of definitions) m.set(d.definition_id, d);
    return m;
  }, [definitions]);

  // ---- refresh helpers (declared early so other callbacks can use them) ----
  const refreshGraph = useCallback(async () => {
    const g = await api.getGraph();
    setGraph(g);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.getStatus());
    } catch {
      /* ignore */
    }
  }, []);

  const handleDeleteDynamicPort = useCallback(
    async (nodeId: string, portId: string) => {
      try {
        await api.removeDynamicPort(nodeId, portId);
        await refreshGraph();
      } catch (e) {
        setError(String(e));
      }
    },
    [refreshGraph],
  );

  // Persist draw_commands to the backend when the user draws on a draw-type
  // node's preview canvas.
  const handleDrawCommand = useCallback(
    async (nodeId: string, commands: unknown[]) => {
      try {
        await api.setProperties(nodeId, { draw_commands: commands });
        // update local graph state immediately so the canvas re-renders
        setGraph((g) =>
          g
            ? {
                ...g,
                nodes: g.nodes.map((n) =>
                  n.id === nodeId
                    ? { ...n, properties: { ...n.properties, draw_commands: commands } }
                    : n,
                ),
              }
            : g,
        );
      } catch (e) {
        setError(String(e));
      }
    },
    [],
  );

  // Persist a single property change from an interactive canvas (crop drag,
  // perspective click, etc.) to the backend + local state.
  const handlePropertyChange = useCallback(
    async (nodeId: string, name: string, value: unknown) => {
      // optimistic local update
      setGraph((g) =>
        g
          ? {
              ...g,
              nodes: g.nodes.map((n) =>
                n.id === nodeId
                  ? { ...n, properties: { ...n.properties, [name]: value } }
                  : n,
              ),
            }
          : g,
      );
      try {
        await api.setProperties(nodeId, { [name]: value });
      } catch (e) {
        setError(String(e));
      }
    },
    [],
  );

  // ---- bootstrap -----------------------------------------------------------
  useEffect(() => {
    (async () => {
      try {
        const [nl, g] = await Promise.all([api.listNodes(), api.getGraph()]);
        setDefinitions(nl.nodes);
        setCategories(nl.categories);
        setGraph(g);
        refreshStatus();
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [refreshStatus]);

  // ---- sync rfNodes/rfEdges from backend graph -----------------------------
  const syncFromGraph = useCallback(
    (g: GraphData, outputs: Record<string, unknown> = {}, errors: Record<string, string> = {}) => {
      const positionById = positionsRef.current;
      // build a map from port_id -> output value so we can resolve connected
      // input values for preview (e.g. Draw Mask shows its connected image)
      const portValueByPortId = new Map<string, unknown>();
      for (const n of g.nodes) {
        for (const op of n.outputs) {
          const key = `${n.id}.${op.name}`;
          if (key in outputs) portValueByPortId.set(op.id, outputs[key]);
        }
      }
      const nextNodes: RFNode<CustomNodeData>[] = g.nodes.map((gn) => {
        const def = defMap.get(gn.definition_id);
        const connsToMe = g.connections.filter((c) => c.to_node_id === gn.id);
        const connectedInputPortIds = new Set(connsToMe.map((c) => c.to_port_id));
        // collect output values keyed by port name for this node
        const outputValues: Record<string, unknown> = {};
        for (const op of gn.outputs) {
          const key = `${gn.id}.${op.name}`;
          if (key in outputs) outputValues[op.name] = outputs[key];
        }
        // collect input values (resolved from connections / ComboBox sources)
        // for preview purposes (image inputs).
        const inputValues: Record<string, unknown> = {};
        const inputSources = (gn.properties.input_sources || {}) as Record<string, string>;
        for (const ip of gn.inputs) {
          // 1. direct connection
          const conn = connsToMe.find((c) => c.to_port_id === ip.id);
          if (conn) {
            const val = portValueByPortId.get(conn.from_port_id);
            if (val !== undefined) inputValues[ip.name] = val;
            continue;
          }
          // 2. ComboBox source "node_id.port_name"
          const src = inputSources[ip.name];
          if (src) {
            const [srcNodeId, srcPortName] = src.split(".", 1);
            const srcNode = g.nodes.find((n) => n.id === srcNodeId);
            const srcPort = srcNode?.outputs.find((p) => p.name === srcPortName);
            if (srcPort) {
              const val = portValueByPortId.get(srcPort.id);
              if (val !== undefined) inputValues[ip.name] = val;
            }
          }
        }
        const displayText = outputs[`${gn.id}__display_text__`] as string | undefined;
        const pos = positionById.get(gn.id) || gn.position;
        return {
          id: gn.id,
          type: "custom",
          position: pos,
          data: {
            node: gn,
            category: def?.category || "general",
            isSelected: gn.id === selectedId,
            hasError: !!errors[gn.id],
            errorMessage: errors[gn.id],
            outputValues,
            inputValues,
            displayText: displayText as string | undefined,
            comment: (gn.properties.__comment__ as string) || undefined,
            connectedInputPortIds,
            available: def?.available !== false,
            resizable: def?.inputs?.length ? undefined : undefined,
            onDeleteDynamicPort: handleDeleteDynamicPort,
            onDrawCommand: handleDrawCommand,
            onPropertyChange: handlePropertyChange,
          },
        };
      });
      setRfNodes(nextNodes);

      const nextEdges: Edge[] = g.connections.map((c) => {
        const fromNode = g.nodes.find((n) => n.id === c.from_node_id);
        const toNode = g.nodes.find((n) => n.id === c.to_node_id);
        const fromPort = fromNode?.outputs.find((p) => p.id === c.from_port_id) || fromNode?.inputs.find((p) => p.id === c.from_port_id);
        const toPort = toNode?.inputs.find((p) => p.id === c.to_port_id);
        const color = typeColor(fromPort?.data_type);
        return {
          id: c.id,
          source: c.from_node_id,
          sourceHandle: c.from_port_id,
          target: c.to_node_id,
          targetHandle: c.to_port_id,
          style: { stroke: color, strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color },
        };
      });
      setRfEdges(nextEdges);
    },
    [defMap, selectedId, handleDeleteDynamicPort, handleDrawCommand, handlePropertyChange, setRfNodes, setRfEdges],
  );

  // keep positionsRef in sync with rfNodes positions
  useEffect(() => {
    const m = new Map<string, { x: number; y: number }>();
    for (const n of rfNodes) m.set(n.id, n.position);
    positionsRef.current = m;
  }, [rfNodes]);

  useEffect(() => {
    if (graph) {
      syncFromGraph(graph, result?.outputs || {}, result?.errors || {});
    }
  }, [graph, selectedId, result, syncFromGraph]);

  // ---- handlers ------------------------------------------------------------
  const handleAddNode = useCallback(
    async (def: NodeDefinition) => {
      try {
        // place near center with slight offset based on count
        const offset = (graph?.nodes.length || 0) * 30;
        await api.addNode(def.definition_id, { x: 120 + offset, y: 120 + offset }, def.display_name);
        await refreshGraph();
      } catch (e) {
        setError(String(e));
      }
    },
    [graph, refreshGraph],
  );

  const handleConnect = useCallback(
    async (conn: Connection) => {
      if (!conn.source || !conn.target || !conn.sourceHandle || !conn.targetHandle) return;
      try {
        const r = await api.addConnection(conn.source, conn.sourceHandle, conn.target, conn.targetHandle);
        if ("detail" in r) {
          setError(r.detail);
          return;
        }
        // auto-expand may have added a port -> refresh
        await refreshGraph();
      } catch (e) {
        setError(String(e));
      }
    },
    [refreshGraph],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      for (const c of changes) {
        if (c.type === "remove") {
          api.removeConnection(c.id).then(refreshGraph);
        }
      }
      onEdgesChange(changes);
    },
    [onEdgesChange, refreshGraph],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // handle deletions via backend; positions are local
      for (const c of changes) {
        if (c.type === "remove") {
          api.removeNode(c.id).then(refreshGraph);
        }
        // when a node is dragged, persist the new position to the backend
        // so Save/Load/refresh preserve the layout.
        if (c.type === "position" && c.position) {
          // update positionsRef immediately so syncFromGraph doesn't override
          positionsRef.current.set(c.id, c.position);
          // fire-and-forget the backend update (don't await — would lag the drag)
          // Use a lightweight PUT that only sets position via the properties endpoint.
          // The backend Graph stores position on the Node model; we update it
          // through a dedicated position endpoint.
          api.updateNodePosition(c.id, c.position).catch(() => {});
        }
      }
      onNodesChange(changes);
    },
    [onNodesChange, refreshGraph],
  );

  const handleRenameNode = useCallback(
    async (id: string, name: string) => {
      // update via properties (name is on the node, not properties). Use a lightweight approach:
      // re-add is not possible, so we patch through setProperties? name is top-level.
      // We'll update local graph node name and push via save-load roundtrip is overkill.
      // Instead, expose via a dedicated update: reuse setProperties is wrong.
      // Simplest: update the in-memory backend node directly is not exposed.
      // We'll skip rename persistence for now and just update locally.
      setGraph((g) =>
        g
          ? { ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, name } : n)) }
          : g,
      );
    },
    [],
  );

  const onNodeClick = useCallback((_: unknown, n: RFNode) => {
    setSelectedId(n.id);
  }, []);
  const onPaneClick = useCallback(() => setSelectedId(null), []);

  // ---- execution -----------------------------------------------------------
  // Fast frame runner: only calls startFrame + executeStep, updates result.
  // Does NOT call refreshGraph (which rebuilds all ReactFlow nodes) — the
  // existing syncFromGraph effect already picks up new outputs from `result`.
  const runOneFrame = useCallback(async (fast = false): Promise<ExecutionResult | null> => {
    try {
      await api.startFrame();
      let last: ExecutionResult | null = null;
      for (let i = 0; i < 20; i++) {
        const r = await api.executeStep();
        last = r;
        setResult(r);
        if (r.status === "frame_complete" || r.status === "idle" || r.status === "exhausted") {
          break;
        }
      }
      if (!fast) {
        // full refresh only in step mode (not streaming)
        await refreshGraph();
        await refreshStatus();
      }
      return last;
    } catch (e) {
      setError(String(e));
      return null;
    }
  }, [refreshGraph, refreshStatus]);

  const handleStep = useCallback(async () => {
    setRunning(false);
    runningRef.current = false;
    await runOneFrame(false);
  }, [runOneFrame]);

  const handleReset = useCallback(async () => {
    setRunning(false);
    runningRef.current = false;
    await api.resetFrame();
    setResult(null);
    await refreshGraph();
    await refreshStatus();
  }, [refreshGraph, refreshStatus]);

  const handleAutoLayout = useCallback(async () => {
    try {
      const r = await api.autoLayout({ direction: "LR" });
      // Update cached positions so syncFromGraph doesn't override them
      const m = new Map<string, { x: number; y: number }>();
      for (const [id, pos] of Object.entries(r.positions)) {
        m.set(id, { x: pos.x, y: pos.y });
      }
      positionsRef.current = m;
      await refreshGraph();
    } catch (e) {
      setError(String(e));
    }
  }, [refreshGraph]);

  const handleRun = useCallback(async () => {
    setRunning(true);
    runningRef.current = true;
    let frameCount = 0;
    while (runningRef.current) {
      const r = await runOneFrame(true);
      if (!r || r.status === "exhausted") break;
      frameCount++;
      // refresh status (frame counter) every 30 frames — cheap
      if (frameCount % 30 === 0) {
        refreshStatus();
      }
      // no artificial delay — the source node (webcam) naturally throttles
      // to the camera framerate; executeStep + HTTP round-trip is enough
      // yield to the browser so the UI can paint
      await new Promise((res) => setTimeout(res, 0));
    }
    // final full refresh when stopped
    await refreshGraph();
    await refreshStatus();
    setRunning(false);
    runningRef.current = false;
  }, [runOneFrame, refreshGraph, refreshStatus]);

  const handleStop = useCallback(() => {
    setRunning(false);
    runningRef.current = false;
  }, []);

  const handleSave = useCallback(() => {
    // Save client-side via Blob download (no hardcoded backend path).
    // The graph is fetched from the backend (which holds the source of truth)
    // then downloaded as a .json file the user can place anywhere.
    (async () => {
      try {
        const g = await api.getGraph();
        const blob = new Blob([JSON.stringify(g, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const now = new Date();
        const ts = now.getFullYear().toString() +
          (now.getMonth() + 1).toString().padStart(2, "0") +
          now.getDate().toString().padStart(2, "0") + "_" +
          now.getHours().toString().padStart(2, "0") +
          now.getMinutes().toString().padStart(2, "0");
        const a = document.createElement("a");
        a.href = url;
        a.download = `graph_${ts}.json`;
        a.click();
        URL.revokeObjectURL(url);
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);

  const loadFileInputRef = useRef<HTMLInputElement | null>(null);
  const handleLoad = useCallback(() => {
    // open the file dialog
    loadFileInputRef.current?.click();
  }, []);

  const handleLoadFile = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = ""; // reset for re-select
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await api.loadGraph(data);
      // clear cached positions so the freshly-loaded positions from the
      // backend are used (otherwise stale positions from the previous graph
      // would override them and all nodes collapse to one spot).
      positionsRef.current = new Map();
      await refreshGraph();
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, [refreshGraph]);

  // ---- right-click context menu on nodes ----
  const onNodeContextMenu = useCallback((event: React.MouseEvent, node: RFNode) => {
    event.preventDefault();
    setContextMenu({ nodeId: node.id, x: event.clientX, y: event.clientY });
  }, []);

  const onEdgeContextMenu = useCallback((event: React.MouseEvent, edge: Edge) => {
    event.preventDefault();
    setEdgeContextMenu({ edgeId: edge.id, x: event.clientX, y: event.clientY });
  }, []);

  const closeEdgeContextMenu = useCallback(() => setEdgeContextMenu(null), []);

  const handleEdgeContextDelete = useCallback(async () => {
    if (!edgeContextMenu) return;
    await api.removeConnection(edgeContextMenu.edgeId);
    await refreshGraph();
    closeEdgeContextMenu();
  }, [edgeContextMenu, refreshGraph, closeEdgeContextMenu]);

  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  const handleContextDelete = useCallback(async () => {
    if (!contextMenu) return;
    await api.removeNode(contextMenu.nodeId);
    await refreshGraph();
    closeContextMenu();
  }, [contextMenu, refreshGraph, closeContextMenu]);

  const handleContextDuplicate = useCallback(async () => {
    if (!contextMenu || !graph) return;
    const src = graph.nodes.find((n) => n.id === contextMenu.nodeId);
    if (!src) { closeContextMenu(); return; }
    try {
      const def = defMap.get(src.definition_id);
      if (!def) { closeContextMenu(); return; }
      const newNode = await api.addNode(src.definition_id, {
        x: src.position.x + 50,
        y: src.position.y + 50,
      }, src.name + " copy");
      // copy properties (except input_sources which are node-specific)
      const props = { ...src.properties };
      delete props.input_sources;
      await api.setProperties(newNode.id, props);
      await refreshGraph();
    } catch (e) {
      setError(String(e));
    }
    closeContextMenu();
  }, [contextMenu, graph, defMap, refreshGraph, closeContextMenu]);

  const handleContextComment = useCallback(() => {
    if (!contextMenu || !graph) return;
    const src = graph.nodes.find((n) => n.id === contextMenu.nodeId);
    if (!src) { closeContextMenu(); return; }
    const current = (src.properties.__comment__ as string) || "";
    setCommentModal({ nodeId: contextMenu.nodeId, value: current });
    closeContextMenu();
  }, [contextMenu, graph, closeContextMenu]);

  const handleCommentSubmit = useCallback(async () => {
    if (!commentModal) return;
    try {
      await api.setProperties(commentModal.nodeId, { __comment__: commentModal.value });
      await refreshGraph();
    } catch (e) {
      setError(String(e));
    }
    setCommentModal(null);
  }, [commentModal, refreshGraph]);

  // ---- derived -------------------------------------------------------------
  const selectedNode = useMemo(
    () => graph?.nodes.find((n) => n.id === selectedId) || null,
    [graph, selectedId],
  );
  const selectedDef = useMemo(
    () => (selectedNode ? defMap.get(selectedNode.definition_id) || null : null),
    [selectedNode, defMap],
  );
  const selectedConnectedInputs = useMemo(() => {
    if (!selectedNode || !graph) return new Set<string>();
    return new Set(
      graph.connections
        .filter((c) => c.to_node_id === selectedNode.id)
        .map((c) => c.to_port_id),
    );
  }, [selectedNode, graph]);

  const statusBadge = useMemo(() => {
    const s: ExecutionStatus | null = result?.status || null;
    if (running) return { label: "RUNNING", cls: "bg-cyan-500/20 text-cyan-300 border-cyan-500/40" };
    if (s === "frame_complete") return { label: "FRAME COMPLETE", cls: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40" };
    if (s === "idle") return { label: "IDLE", cls: "bg-zinc-500/20 text-zinc-300 border-zinc-500/40" };
    if (s === "exhausted") return { label: "EXHAUSTED", cls: "bg-amber-500/20 text-amber-300 border-amber-500/40" };
    return { label: "READY", cls: "bg-zinc-700/40 text-zinc-400 border-zinc-600/40" };
  }, [result, running]);

  return (
    <div className="h-screen flex flex-col bg-zinc-950 text-zinc-100 overflow-hidden" onClick={() => { if (contextMenu) closeContextMenu(); if (edgeContextMenu) closeEdgeContextMenu(); }}>
      {/* top bar */}
      <header className="h-14 shrink-0 border-b border-zinc-800 bg-zinc-900/80 backdrop-blur flex items-center gap-3 px-4">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-md bg-gradient-to-br from-cyan-500 to-emerald-500 flex items-center justify-center">
            <Activity className="w-4 h-4 text-white" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-tight">Multimodal Node Editor</div>
            <div className="text-[10px] text-zinc-500 leading-tight">VisionMaster-style · sharded execution</div>
          </div>
        </div>
        <div className="h-6 w-px bg-zinc-800 mx-1" />
        {/* execution controls */}
        <div className="flex items-center gap-1.5">
          {running ? (
            <Button size="sm" variant="destructive" className="h-8 gap-1.5" onClick={handleStop}>
              <Square className="w-3.5 h-3.5" /> Stop
            </Button>
          ) : (
            <Button size="sm" className="h-8 gap-1.5 bg-cyan-600 hover:bg-cyan-500" onClick={handleRun}>
              <Play className="w-3.5 h-3.5" /> Run
            </Button>
          )}
          <Button size="sm" variant="outline" className="h-8 gap-1.5" onClick={handleStep} disabled={running}>
            <SkipForward className="w-3.5 h-3.5" /> Step Frame
          </Button>
          <Button size="sm" variant="ghost" className="h-8 gap-1.5" onClick={handleReset} disabled={running}>
            <RotateCcw className="w-3.5 h-3.5" /> Reset
          </Button>
          <Button size="sm" variant="ghost" className="h-8 gap-1.5" onClick={handleAutoLayout} disabled={running} title="Auto-arrange all nodes in a layered layout">
            <LayoutGrid className="w-3.5 h-3.5" /> Auto Layout
          </Button>
        </div>
        <div className="h-6 w-px bg-zinc-800 mx-1" />
        {/* save/load */}
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" className="h-8 gap-1.5" onClick={handleSave}>
            <Save className="w-3.5 h-3.5" /> Save
          </Button>
          <Button size="sm" variant="ghost" className="h-8 gap-1.5" onClick={handleLoad}>
            <FolderOpen className="w-3.5 h-3.5" /> Load
          </Button>
        </div>

        {/* status */}
        <div className="ml-auto flex items-center gap-2">
          {error && (
            <span className="text-[11px] text-red-400 max-w-[260px] truncate" title={error}>
              {error}
            </span>
          )}
          {status && (
            <span className="text-[11px] text-zinc-500">frame #{status.frame_id}</span>
          )}
          <Badge className={`text-[10px] border ${statusBadge.cls}`}>{statusBadge.label}</Badge>
        </div>
      </header>

      {/* main */}
      <div className="flex-1 flex min-h-0">
        {/* left palette */}
        <aside className="w-60 shrink-0 border-r border-zinc-800 bg-zinc-900/40">
          <NodePalette definitions={definitions} categories={categories} onAdd={handleAddNode} />
        </aside>

        {/* canvas */}
        <main className="flex-1 min-w-0 relative">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={handleConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onNodeContextMenu={onNodeContextMenu}
            onEdgeContextMenu={onEdgeContextMenu}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
            colorMode="system"
            defaultEdgeOptions={{ style: { strokeWidth: 2 } }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#27272a" />
            <Controls className="!bg-zinc-900 !border-zinc-700" />
            <MiniMap
              className="!bg-zinc-900 !border-zinc-700"
              nodeColor={(n) => {
                const d = (n.data as unknown as CustomNodeData) || null;
                const cat = d?.category;
                const colors: Record<string, string> = {
                  source: "#22c55e", math: "#3b82f6", image: "#ec4899",
                  display: "#a855f7", general: "#64748b",
                  communication: "#f59e0b", utility: "#14b8a6",
                  audio: "#06b6d4", text: "#eab308", openai: "#10b981",
                  ai: "#8b5cf6",
                };
                // also handle dotted category ids like "image.input" -> "image"
                const top = cat ? cat.split(".")[0] : "general";
                return colors[cat || "general"] || colors[top] || "#64748b";
              }}
            />
          </ReactFlow>
          {/* execution result footer strip */}
          <ExecResultStrip result={result} />
        </main>

        {/* right panel — tabbed: Config / Models */}
        <aside className="w-72 shrink-0 border-l border-zinc-800 bg-zinc-900/40 flex flex-col overflow-hidden min-w-0">
          {/* tab bar */}
          <div className="flex border-b border-zinc-800 shrink-0">
            <button
              className={`flex-1 py-2 text-[11px] font-medium transition-colors ${
                rightTab === "config"
                  ? "text-cyan-300 border-b-2 border-cyan-400 bg-zinc-800/40"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
              onClick={() => setRightTab("config")}
            >
              Config
            </button>
            <button
              className={`flex-1 py-2 text-[11px] font-medium transition-colors flex items-center justify-center gap-1 ${
                rightTab === "models"
                  ? "text-cyan-300 border-b-2 border-cyan-400 bg-zinc-800/40"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
              onClick={() => setRightTab("models")}
            >
              Models
            </button>
          </div>
          {/* tab content */}
          <div className="flex-1 min-h-0">
            {rightTab === "config" ? (
              <ConfigPanel
                node={selectedNode}
                definition={selectedDef}
                connectedInputPortIds={selectedConnectedInputs}
                onNodeChanged={refreshGraph}
                onRenameNode={handleRenameNode}
              />
            ) : (
              <ModelsPanel active={rightTab === "models"} />
            )}
          </div>
        </aside>
      </div>

      {/* hidden file input for Load */}
      <input
        type="file"
        accept=".json"
        ref={loadFileInputRef}
        onChange={handleLoadFile}
        style={{ display: "none" }}
      />

      {/* right-click context menu */}
      {contextMenu && (
        <div
          className="fixed z-50 min-w-[140px] rounded-md border border-zinc-700 bg-zinc-900 shadow-lg py-1 text-xs"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-zinc-800 text-zinc-200"
            onClick={handleContextComment}
          >
            {graph?.nodes.find((n) => n.id === contextMenu.nodeId)?.properties?.__comment__ ? "Edit Comment" : "Add Comment"}
          </button>
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-zinc-800 text-zinc-200"
            onClick={handleContextDuplicate}
          >
            Duplicate
          </button>
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-red-950/50 text-red-400"
            onClick={handleContextDelete}
          >
            Delete
          </button>
        </div>
      )}

      {/* edge right-click context menu */}
      {edgeContextMenu && (
        <div
          className="fixed z-50 min-w-[140px] rounded-md border border-zinc-700 bg-zinc-900 shadow-lg py-1 text-xs"
          style={{ left: edgeContextMenu.x, top: edgeContextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-red-950/50 text-red-400"
            onClick={handleEdgeContextDelete}
          >
            Delete Connection
          </button>
        </div>
      )}

      {/* comment modal */}
      {commentModal && (
        <div
          className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
          onClick={() => setCommentModal(null)}
        >
          <div
            className="bg-zinc-900 border border-zinc-700 rounded-lg p-4 w-full max-w-sm space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm font-semibold text-zinc-100">Comment</div>
            <textarea
              className="w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 min-h-[80px]"
              value={commentModal.value}
              onChange={(e) => setCommentModal({ ...commentModal, value: e.target.value })}
              placeholder="Enter comment..."
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleCommentSubmit(); }
                else if (e.key === "Escape") setCommentModal(null);
              }}
            />
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => setCommentModal(null)}>Cancel</Button>
              <Button size="sm" className="h-8 text-xs bg-cyan-600 hover:bg-cyan-500" onClick={handleCommentSubmit}>OK</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Execution result footer strip
// ---------------------------------------------------------------------------
function ExecResultStrip({ result }: { result: ExecutionResult | null }) {
  if (!result) return null;
  const times = Object.values(result.node_times).sort((a, b) => a.order - b.order);
  return (
    <div className="absolute bottom-3 left-3 pointer-events-none max-w-[calc(100%-1.5rem)]">
      <div className="pointer-events-auto inline-flex items-center gap-3 rounded-full border border-zinc-800 bg-zinc-900/90 backdrop-blur px-3 py-1.5 text-[11px] overflow-x-auto max-w-full">
        <span className="shrink-0">
          <span className="text-zinc-500">exec</span>{" "}
          <span className="text-emerald-300">{result.executed_nodes.length}</span>
          {result.skipped_nodes.length > 0 && (
            <span className="text-amber-300"> · skip {result.skipped_nodes.length}</span>
          )}
          {result.waiting_nodes.length > 0 && (
            <span className="text-cyan-300"> · wait {result.waiting_nodes.length}</span>
          )}
          <span className="text-zinc-500"> · {result.elapsed_ms.toFixed(1)}ms</span>
        </span>
        {Object.keys(result.errors).length > 0 && (
          <span className="shrink-0 text-red-400">errs {Object.keys(result.errors).length}</span>
        )}
        {times.length > 0 && (
          <span className="shrink-0 text-zinc-500 hidden md:inline">
            {times.slice(0, 4).map((t) => `${t.name} ${t.time.toFixed(0)}ms`).join(" · ")}
          </span>
        )}
      </div>
    </div>
  );
}

export default function NodeEditorPage() {
  return (
    <ReactFlowProvider>
      <EditorInner />
    </ReactFlowProvider>
  );
}
