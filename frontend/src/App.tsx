import { useState } from "react";
import ImportZone from "./pages/ImportZone/ImportZone";
import DetailPanel from "./pages/DetailPanel/DetailPanel";
import type { TreeNode } from "./types";

export default function App() {
  const [selectedFile, setSelectedFile] = useState<TreeNode | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const bump = () => setDataVersion((v) => v + 1);

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-title">第二大脑</span>
        <nav className="app-nav">
          <button className="active">工作台</button>
        </nav>
      </header>

      <div className="app-body">
        {/* 左侧：项目文件（原始文件导入区） */}
        <aside className="app-sidebar">
          <ImportZone onSelectFile={setSelectedFile} onDataChanged={bump} />
        </aside>

        {/* 右侧：内容可视化（编辑） + 知识库笔记 + 图谱显示 */}
        <main className="app-detail">
          <DetailPanel selectedFile={selectedFile} dataVersion={dataVersion} onDataChanged={bump} />
        </main>
      </div>
    </div>
  );
}