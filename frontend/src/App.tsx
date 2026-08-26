import { useEffect, useState } from "react";
import ImportZone from "./pages/ImportZone/ImportZone";
import DetailPanel from "./pages/DetailPanel/DetailPanel";
import AssistantPanel from "./components/AssistantPanel/AssistantPanel";
import AdvancedPanel from "./components/AdvancedPanel/AdvancedPanel";
import { getGraphStatus } from "./api/graph";
import type { TreeNode } from "./types";

export default function App() {
  const [selectedFile, setSelectedFile] = useState<TreeNode | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const [graphReady, setGraphReady] = useState(false);
  const [graphSignal, setGraphSignal] = useState(0);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const bump = () => setDataVersion((v) => v + 1);

  // 图谱就绪状态 → 决定图谱按钮是否可点
  useEffect(() => {
    getGraphStatus()
      .then((s) => setGraphReady(Boolean(s.ready)))
      .catch(() => setGraphReady(false));
  }, []);

  function openGraph() {
    setGraphSignal((v) => v + 1);
  }

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-title">第二大脑</span>
        <nav className="app-nav">
          <button className="active">工作台</button>
        </nav>
        <div className="app-header-right">
          <button disabled={!graphReady} onClick={openGraph} title={graphReady ? "查看知识图谱" : "未构建图谱（需 API Key 且已写入知识库）"}>
            🕸 图谱
          </button>
          <button
            className={advancedOpen ? "active" : ""}
            onClick={() => setAdvancedOpen((o) => !o)}
            title="周期回顾 / 健康检查 / 网页剪藏"
          >
            🛠 进阶
          </button>
          <button
            className={assistantOpen ? "active" : ""}
            onClick={() => setAssistantOpen((o) => !o)}
            title="AI 助手"
          >
            🤖 AI
          </button>
        </div>
      </header>

      <div className="app-body">
        {/* 左侧：项目文件（原始文件导入区） */}
        <aside className="app-sidebar">
          <ImportZone onSelectFile={setSelectedFile} onDataChanged={bump} />
        </aside>

        {/* 右侧：内容可视化（编辑） + 知识库笔记 + 图谱显示 */}
        <main className="app-detail">
          <DetailPanel
            selectedFile={selectedFile}
            dataVersion={dataVersion}
            graphSignal={graphSignal}
            onDataChanged={bump}
          />
        </main>
      </div>

      {assistantOpen && <AssistantPanel onClose={() => setAssistantOpen(false)} />}
      {advancedOpen && (
        <AdvancedPanel onClose={() => setAdvancedOpen(false)} onDataChanged={bump} />
      )}
    </div>
  );
}