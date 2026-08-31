import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type EventObject, type ElementDefinition } from "cytoscape";
import * as graphApi from "../../api/graph";
import { listNotes } from "../../api/kb";
import type { GraphEdge, GraphNode, GraphNodeDetail } from "../../types";
import LoadingOverlay from "../../components/LoadingOverlay";
import "./GraphView.css";

const TYPE_COLORS: Record<string, string> = {
  概念: "#2563eb",
  人物: "#059669",
  组织: "#7c3aed",
  地点: "#d97706",
  文档: "#0e7490",
  技术: "#dc2626",
};

// 拖拽联动深度：拖动某节点时，其 N 跳关联节点会整体跟随移动（更接近 Neo4j 手感）
const NEIGHBOR_DRAG_DEPTH = 2;
// 搜索「保留关联节点」时的扩散深度
const KEEP_NEIGHBOR_DEPTH = 1;

function colorFor(type?: string): string {
  if (type && TYPE_COLORS[type]) return TYPE_COLORS[type];
  return "#64748b";
}

/**
 * 费马螺旋圆形打包（sunflower / 圆形堆积）：
 * 把全部节点按近似均匀密度铺进一个实心圆盘——中心密、外围疏，无空洞、无直线、无同心环，
 * 保证"所有节点聚成一个紧凑球状"。相比基础 force-directed，
 * 力导向对孤立/稀疏节点只会把它们排到同一平衡半径绕成一圈，
 * 这里用几何打包从根上避免"排成直线"，且之后仍可手动拖拽。
 */
function layoutCirclePack(cy: Core) {
  const nodes = cy.nodes();
  const n = nodes.length;
  if (n === 0) return;
  const spacing = 34; // 相邻节点圆心间距（节距），随节点大小/标签可调
  const goldenAngle = Math.PI * (3 - Math.sqrt(5)); // 约 137.5°，均匀铺点点位
  nodes.forEach((node, i) => {
    const r = spacing * Math.sqrt(i);
    const theta = i * goldenAngle;
    node.position({ x: r * Math.cos(theta), y: r * Math.sin(theta) });
  });
  cy.fit(cy.elements(), 60);
}

type SearchMode = "fuzzy" | "exact";

interface VisibleGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export default function GraphView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const dragRef = useRef<{
    id: string;
    start: { x: number; y: number };
    links: { node: cytoscape.NodeSingular; depth: number; base: { x: number; y: number } }[];
  } | null>(null);

  const [loading, setLoading] = useState(false);
  const [buildGraphing, setBuildGraphing] = useState(false);
  const [error, setError] = useState("");
  const [allNodes, setAllNodes] = useState<GraphNode[]>([]);
  const [allEdges, setAllEdges] = useState<GraphEdge[]>([]);
  const [ready, setReady] = useState(false); // cytoscape 已初始化
  const [selected, setSelected] = useState<GraphNodeDetail | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<{
    source: string;
    target: string;
    relation_type: string;
    description?: string;
  } | null>(null);
  // 图谱删除：确认弹窗 + 删除中状态
  const [confirmDelete, setConfirmDelete] = useState<{
    kind: "node" | "edge";
    label: string;
  } | null>(null);
  const [deleting, setDeleting] = useState(false);

  // ---- 过滤/搜索状态 ----
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("fuzzy");
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [keepNeighbors, setKeepNeighbors] = useState(true);

  const nodeTypes = useMemo(() => {
    const set = new Set<string>();
    allNodes.forEach((n) => set.add(n.entity_type || "其他"));
    return Array.from(set).sort();
  }, [allNodes]);

  // ---------- 过滤管道：先按类别隐藏，再按搜索命中（可扩散到邻居）----------
  const visible = useMemo<VisibleGraph>(() => {
    const q = query.trim();
    const searching = q !== "";
    const byType = (n: GraphNode) => !hiddenTypes.has(n.entity_type || "其他");

    const pool = allNodes.filter(byType);
    const poolById = new Map(pool.map((n) => [String(n.id), n]));

    const maxTime = Date.now() + 1500; // 防御性上限
    const idSet = new Set<string>();

    if (!searching) {
      for (const n of pool) idSet.add(String(n.id));
    } else {
      const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
      const matched = pool.filter((n) => {
        const name = (n.name || "").trim().toLowerCase();
        if (searchMode === "exact") return name === q.toLowerCase();
        return tokens.every((t) => name.includes(t));
      });
      if (matched.length === 0) return { nodes: [], edges: [] };
      for (const n of matched) idSet.add(String(n.id));

      if (keepNeighbors) {
        let frontier = matched.map((n) => String(n.id));
        for (let d = 0; d < KEEP_NEIGHBOR_DEPTH && Date.now() < maxTime; d++) {
          const next = new Set<string>();
          for (const e of allEdges) {
            const s = String(e.source);
            const t = String(e.target);
            if (frontier.includes(s) && poolById.has(t) && !idSet.has(t)) next.add(t);
            if (frontier.includes(t) && poolById.has(s) && !idSet.has(s)) next.add(s);
          }
          if (next.size === 0) break;
          next.forEach((id) => idSet.add(id));
          frontier = Array.from(next);
        }
      }
    }

    const nodes = allNodes.filter((n) => idSet.has(String(n.id)));
    const edges = allEdges.filter(
      (e) => idSet.has(String(e.source)) && idSet.has(String(e.target)),
    );
    return { nodes, edges };
  }, [allNodes, allEdges, query, searchMode, hiddenTypes, keepNeighbors]);

  // 把当前可见集合应用到 cytoscape（重建 + 平滑布局）
  const applyToCy = useCallback(
    (v: VisibleGraph) => {
      const cy = cyRef.current;
      if (!cy) return;
      const idSet = new Set(v.nodes.map((n) => String(n.id)));
      const elements: ElementDefinition[] = v.nodes.map((n) => ({
        data: { id: String(n.id), name: n.name, color: colorFor(n.entity_type) },
      }));
      elements.push(
        ...v.edges
          .filter((e) => idSet.has(String(e.source)) && idSet.has(String(e.target)))
          .map((e) => ({
            data: {
              id: String(e.id),
              source: String(e.source),
              target: String(e.target),
              relation_type: e.relation_type || "相关",
              description: e.description,
            },
          })),
      );
      cy.elements().remove();
      if (elements.length === 0) return;
      cy.add(elements);
      // 费马螺旋圆形打包：所有节点聚成紧凑实心球状（中心密、外围疏，无直线/无同心环）
      layoutCirclePack(cy);
    },
    [],
  );

  useEffect(() => {
    if (ready) applyToCy(visible);
  }, [ready, visible, applyToCy]);

  // ---------- cytoscape 初始化 ----------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ci = cytoscape({
      container,
      style: [
        {
          selector: "node",
          style: {
            label: "data(name)",
            width: 20,
            height: 20,
            "background-color": "data(color)",
            "text-valign": "bottom",
            "text-halign": "center",
            "font-size": 11,
            color: "#334155",
            "text-max-width": "120px",
            "text-wrap": "ellipsis",
            "border-width": 1,
            "border-color": "#ffffff",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#cbd5e1",
            "target-arrow-color": "#cbd5e1",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.8,
            "curve-style": "bezier",
            label: "data(relation_type)",
            "font-size": 9,
            color: "#94a3b8",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
          },
        },
      ],
      layout: {
        name: "preset",
      },
      wheelSensitivity: 0.45,
      boxSelectionEnabled: true,
      autoungrabify: false,
    });

    ci.on("tap", "node", (ev: EventObject) => {
      const name = (ev.target as cytoscape.NodeSingular).data("name") as string;
      openNodeDetail(name);
    });
    ci.on("tap", "edge", (ev: EventObject) => {
      const data = (ev.target as cytoscape.EdgeSingular).data();
      setSelectedEdge({
        source: data.source as string,
        target: data.target as string,
        relation_type: (data.relation_type as string) || "相关",
        description: data.description as string | undefined,
      });
    });
    ci.on("tap", (ev: EventObject) => {
      if (ev.target === ci) {
        setSelected(null);
        setSelectedEdge(null);
      }
    });

    // ---------- Neo4j 式联动拖拽：拖动节点时，其关联节点整体跟随 ----------
    ci.on("grab", "node", (ev: EventObject) => {
      // 先停掉可能在跑的布局动画（尤其布局动画尚未完成时），避免其与手动位移互相干扰；
      // 之后再进入纯手动拖拽，保证关联节点跟随幅度不超过被拖动节点。
      ci.stop();
      const node = ev.target as cytoscape.NodeSingular;
      const links: { node: cytoscape.NodeSingular; depth: number; base: { x: number; y: number } }[] = [];
      const seen = new Set<string>([node.id()]);
      let frontier = [node];
      for (let d = 1; d <= NEIGHBOR_DRAG_DEPTH; d++) {
        const next: cytoscape.NodeSingular[] = [];
        for (const n of frontier) {
          for (const nb of n.openNeighborhood().nodes()) {
            if (!seen.has(nb.id())) {
              seen.add(nb.id());
              next.push(nb);
            }
          }
        }
        for (const nb of next) {
          const p = nb.position();
          links.push({ node: nb, depth: d, base: { x: p.x, y: p.y } });
        }
        if (next.length === 0) break;
        frontier = next;
      }
      dragRef.current = { id: node.id(), start: { x: node.position("x"), y: node.position("y") }, links };
    });
    ci.on("drag", "node", (ev: EventObject) => {
      const ds = dragRef.current;
      const node = ev.target as cytoscape.NodeSingular;
      if (!ds || ds.id !== node.id()) return;
      const dx = node.position("x") - ds.start.x;
      const dy = node.position("y") - ds.start.y;
      for (const l of ds.links) {
        // 阻尼系数随跳数递减，保证关联节点的动作幅度不超过被拖动的节点，
        // 越远越轻，避免"整个网络跟着飞"的生硬感。数值在用户手感反馈基础上上调约 10%。
        const f = l.depth === 1 ? 0.88 : 0.66;
        l.node.position({ x: l.base.x + dx * f, y: l.base.y + dy * f });
      }
    });
    ci.on("free", "node", () => {
      dragRef.current = null;
    });

    cyRef.current = ci;
    setReady(true);
    return () => {
      ci.destroy();
      cyRef.current = null;
      setReady(false);
    };
  }, []);

  async function loadGraph() {
    setLoading(true);
    setError("");
    setSelected(null);
    setSelectedEdge(null);
    try {
      const data = await graphApi.exportGraph();
      setAllNodes(data.nodes);
      setAllEdges(data.edges);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function openNodeDetail(name: string) {
    try {
      const d = await graphApi.getNodeDetail(name);
      setSelected(d);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // 手动把整个知识库（所有笔记）重建为图谱：相当于显式的"知识库 → 图谱"入口。
  async function rebuildGraph() {
    setBuildGraphing(true);
    setError("");
    try {
      const { notes } = await listNotes();
      const ids = notes.map((n) => n.id);
      if (ids.length === 0) {
        setError("知识库为空，没有可重建图谱的笔记");
        return;
      }
      const res = await graphApi.buildGraph(ids);
      const failed = (res.results || []).filter((r) => r.status === "error");
      if (failed.length) {
        setError(`重建完成，但 ${failed.length} 篇笔记构建失败（如 ${failed[0].reason || "无原因"}）`);
      }
      await loadGraph();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBuildGraphing(false);
    }
  }

  useEffect(() => {
    loadGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleType(type: string) {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  // ---------- 图谱删除（节点 / 关系边） ----------
  async function onConfirmDelete() {
    if (!confirmDelete) return;
    setDeleting(true);
    setError("");
    try {
      if (confirmDelete.kind === "node" && selected) {
        await graphApi.deleteGraphNode(selected.name);
        setSelected(null);
      } else if (confirmDelete.kind === "edge" && selectedEdge) {
        await graphApi.deleteGraphRelation({
          source: selectedEdge.source,
          target: selectedEdge.target,
          relation_type: selectedEdge.relation_type,
        });
        setSelectedEdge(null);
      }
      setConfirmDelete(null);
      await loadGraph();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  const totalNodes = allNodes.length;
  const totalEdges = allEdges.length;
  const shownNodes = visible.nodes.length;

  return (
    <div className="graph-view">
      <div className="graph-toolbar">
        <span>
          知识图谱（{shownNodes}/{totalNodes} 节点 · {visible.edges.length}/{totalEdges} 关系）
        </span>
        <button className="ghost" onClick={rebuildGraph} disabled={buildGraphing} title="把知识库所有笔记重建为图谱（需配置 DASHSCOPE_API_KEY）">
          {buildGraphing ? "构建中…" : "♻️ 重建图谱"}
        </button>
        <button className="ghost" onClick={loadGraph} disabled={loading}>
          {loading ? "加载中…" : "🔄 刷新"}
        </button>
      </div>

      <div className="graph-controls">
        <div className="graph-search">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索节点名…"
            title="输入节点名，支持空格分隔多关键词"
          />
          <select
            value={searchMode}
            onChange={(e) => setSearchMode(e.target.value as SearchMode)}
            title="精确：节点名完全匹配；模糊：包含关键词即命中"
          >
            <option value="fuzzy">模糊</option>
            <option value="exact">精确</option>
          </select>
          {query && (
            <button className="ghost" onClick={() => setQuery("")} title="清除搜索">
              ✕
            </button>
          )}
        </div>

        <div className="graph-filter">
          <span className="filter-label">类别：</span>
          {nodeTypes.map((t) => {
            const hidden = hiddenTypes.has(t);
            return (
              <button
                key={t}
                className={`type-chip${hidden ? " off" : ""}`}
                onClick={() => toggleType(t)}
                title={hidden ? `显示「${t}」` : `隐藏「${t}」`}
              >
                <i style={{ background: colorFor(t) }} />
                {t}
              </button>
            );
          })}
          <label className="keep-check" title="搜索时保留匹配节点的关联节点，关闭后仅显示匹配节点">
            <input
              type="checkbox"
              checked={keepNeighbors}
              onChange={(e) => setKeepNeighbors(e.target.checked)}
            />
            保留关联
          </label>
          {(hiddenTypes.size > 0 || query) && (
            <button
              className="ghost link"
              onClick={() => {
                setHiddenTypes(new Set());
                setQuery("");
                setKeepNeighbors(true);
              }}
            >
              重置
            </button>
          )}
        </div>
      </div>

      {error && <div className="msg error">{error}</div>}
      {shownNodes === 0 && !loading && !error && (
        <div className="placeholder">
          {totalNodes === 0 ? "暂无图谱数据" : "没有符合条件的节点"}
          <p className="placeholder-sub">
            {totalNodes === 0
              ? "请先在知识库写入笔记并构建图谱，再点击刷新。"
              : "请调整搜索关键词或类别过滤。"}
          </p>
        </div>
      )}
      <div className="graph-canvas" ref={containerRef} />

      {shownNodes > 0 && allNodes.length > 0 && (
        <div className="graph-hint">提示：拖动节点时其关联节点会一起移动；点击节点或连线查看详情。</div>
      )}

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal-box node-detail-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              {selected.name}
              <span className="badge" style={{ background: colorFor(selected.entity_type) }}>
                {selected.entity_type}
              </span>
            </div>
            <div className="modal-body">
              {selected.description && <p className="graph-desc">{selected.description}</p>}
              {selected.edges.length > 0 && (
                <>
                  <div className="modal-sub">关联关系（{selected.edges.length}）</div>
                  <ul className="graph-edge-list">
                    {selected.edges.map((e, i) => (
                      <li key={i}>
                        <code>{e.source}</code> —{e.relation_type}→ <code>{e.target}</code>
                        {e.description && <div className="edge-desc">{e.description}</div>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {selected.documents.length > 0 && (
                <>
                  <div className="modal-sub">来源笔记</div>
                  <ul className="graph-doc-list">
                    {selected.documents.map((d) => (
                      <li key={d.note_id}>
                        <code>{d.note_path}</code>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
            <div className="modal-actions">
              <button
                className="danger"
                onClick={() => setConfirmDelete({ kind: "node", label: selected.name })}
                title="删除该实体节点及其全部关联关系"
              >
                🗑 删除实体
              </button>
              <button className="ghost" onClick={() => setSelected(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedEdge && (
        <div className="modal-overlay" onClick={() => setSelectedEdge(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">关系详情</div>
            <div className="modal-body">
              <p>
                <code>{selectedEdge.source}</code> —{selectedEdge.relation_type}→{" "}
                <code>{selectedEdge.target}</code>
              </p>
              {selectedEdge.description && <p className="graph-desc">{selectedEdge.description}</p>}
            </div>
            <div className="modal-actions">
              <button
                className="danger"
                onClick={() =>
                  setConfirmDelete({
                    kind: "edge",
                    label: `${selectedEdge.source} —${selectedEdge.relation_type}→ ${selectedEdge.target}`,
                  })
                }
                title="删除该关系边"
              >
                🗑 删除关系
              </button>
              <button className="ghost" onClick={() => setSelectedEdge(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div className="modal-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              {confirmDelete.kind === "node" ? "删除实体" : "删除关系"}
            </div>
            <div className="modal-body">
              确认删除 <code>{confirmDelete.label}</code> ？
              <div className="modal-sub warn">
                {confirmDelete.kind === "node"
                  ? "将删除该实体节点及其全部关联关系，不可恢复。"
                  : "将删除该关系边，不可恢复。"}
              </div>
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setConfirmDelete(null)} disabled={deleting}>
                取消
              </button>
              <button className="danger" onClick={onConfirmDelete} disabled={deleting}>
                {deleting ? "删除中…" : "删除"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 阻塞式加载提示：生成图谱（未完成前禁止其他操作） */}
      <LoadingOverlay show={buildGraphing} message={buildGraphing ? "正在生成图谱…" : ""} />
    </div>
  );
}