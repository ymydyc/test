# 第二大脑 · 基于 RAG 的个人知识管理系统

导入原始文件区 → 选择性写入知识库 → 图谱可视化 → 右侧 AI 助手。

## 功能定位

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 阶段一 | 项目骨架 + 原始文件导入区（FR-01） | ✅ |
| 阶段二 | 选择性写入知识库 + 内容区编辑（FR-02/FR-11） | ✅ |
| 阶段三 | 图谱增量构建 + 混合检索底座（FR-03/04/05） | ✅ |
| 阶段四 | 图谱可视化 + AI 助手（FR-06/07/08） | ✅ |
| 阶段五 | 进阶能力 + 交付打磨（FR-09/10/12） | ✅ |
| 阶段六 | 导入区数据 MySQL 数据库化（优化） | ✅ |
| 阶段七 | 用户系统 + 个人空间数据隔离（FR-13/14） | ✅ |
| 阶段八 | 组系统（邀请码共享知识库与图谱，FR-15） | ✅ |

## 技术栈（固定）

后端 Python/FastAPI · 前端 React + TypeScript + Vite · DashScope（`qwen3.7-flash` / `text-embedding-v4`）· Chroma · Neo4j(Docker) · MySQL(RAG) · watchdog

## 环境要求

- Python 3.10+
- Node.js 18+
- MySQL（`127.0.0.1:3306`，凭据通过 `.env` 配置 `MYSQL_USER` / `MYSQL_PASSWORD`，启动自动建库建表 `RAG`）
- Neo4j（Docker，连接串与凭据通过 `.env` 配置）
- DashScope：`.env` 中配置 `DASHSCOPE_API_KEY`

## 目录结构（节选）

```
backend/     FastAPI 后端（分层：core/db/api/schemas/services/...）
frontend/    React + TS + Vite 前端
data/raw/    原始文件区「逻辑根路径」（阶段六起文件内容存 MySQL，不再落盘）
data/kb/     知识库笔记（阶段二起用）
data/input/  AI 生成 md 目录（阶段六起并入导入区 DB，默认子目录 output/）
scripts/     start_dev.ps1 / db_sync.py
```

## 快速开始（开发）

```powershell
# 1. 配置环境变量
copy .env.example .env   # 按需修改（阶段一只需 MySQL 配置）

# 2. 安装 Python 依赖
cd backend
pip install -r requirements.txt

# 3. 安装前端依赖
cd ..\frontend
npm install

# 4. 启动（后端 8000 / 前端 5173，前端已代理 /api）
.\scripts\start_dev.ps1
```

界面：打开 http://localhost:5173 查看「导入区」；后端接口文档 http://localhost:8000/docs 。

## 数据库一致性

```
python scripts/db_sync.py          # 检查 ORM ⇄ DB 差异
python scripts/db_sync.py --apply  # 按 ORM 自动建表/增列（幂等）
```

以 `backend/app/db/` 下的 ORM 模型为唯一权威。

