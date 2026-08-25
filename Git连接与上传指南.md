# Git 连接与上传指南

本项目托管于 GitHub 仓库：`https://github.com/ymydyc/test`（远端为空仓库，由 `ymydyc` 账号创建）。

本文档说明如何在本地连接该仓库、上传代码，以及哪些文件被过滤不上传。

---

## 1. 准备工作

- 安装 [Git](https://git-scm.com/)（本机已安装）
- 安装并登录 [GitHub CLI](https://cli.github.com/)（本机已登录账号 `ymydyc`，含 `repo` 权限）

```powershell
# 确认登录状态
gh auth status
```

> 说明：本项目使用 GitHub CLI 的凭据完成 HTTPS 推送，无需手动输入账号密码。
> 若首次使用，可执行 `gh auth login` 完成登录，或 `gh auth setup-git` 让 Git 使用 gh 凭据。

---

## 2. 首次上传（已执行完毕，可跳过）

以下为首次把本地项目推送到远端仓库的完整流程：

```powershell
# 1) 在项目根目录初始化本地仓库（默认分支 main）
git init -b main

# 2) 添加远端
git remote add origin https://github.com/ymydyc/test.git

# 3) 暂存并提交（.gitignore 已过滤多余文件）
git add -A
git commit -m "init: GraphRAG 知识库项目初始提交"

# 4) 推送
git push -u origin main
```

---

## 3. 日常提交 / 推送

```powershell
git add -A
git commit -m "本次改动说明"
git push
```

> 首次推送后已建立 `origin/main` 跟踪关系，后续直接 `git push` 即可。

---

## 4. 其他电脑克隆本项目

```powershell
git clone https://github.com/ymydyc/test.git
cd test
```

克隆后若需配置本地 Git 身份（按需）：

```powershell
git config user.name "你的名字"
git config user.email "你的邮箱"
```

---

## 5. 查看与重置远端

```powershell
git remote -v          # 查看远端地址
git remote remove origin        # 移除远端
git remote add origin https://github.com/ymydyc/test.git  # 重新添加
```

---

## 6. 已过滤文件清单（.gitignore）

以下文件/目录**不会**被提交，避免泄露隐私与冗余数据：

| 类别 | 过滤内容 | 原因 |
| --- | --- | --- |
| 环境密钥 | `.env` | 含数据库密码等敏感信息（保留 `.env.example` 作为模板） |
| Python 缓存 | `__pycache__/`、`*.py[cod]`、`.pytest_cache/`、`.mypy_cache/`、`.coverage` | 编译与测试缓存，可随时重建 |
| Python 环境 | `.venv/`、`venv/` | 虚拟环境，体积大且因人而异 |
| Node 前端 | `node_modules/`、`dist/`、`*.tsbuildinfo`、`vite.config.js`、`vite.config.d.ts` | 依赖与构建产物；`.js`/`.d.ts` 由 `vite.config.ts` 编译生成 |
| 运行时数据 | `data/raw/`、`data/kb/`、`data/input/`、`data/chroma/`、`data/neo4j/` | 用户文档、知识库内容、向量库、Neo4j 数据库文件，均属本地数据 |
| IDE / 系统 | `.idea/`、`.vscode/`、`.DS_Store`、`Thumbs.db` | 本机工具配置与系统文件 |
| 内部会话记录 | `会话*.txt` | 开发过程对话记录，属隐私内容 |

如需调整过滤规则，编辑项目根目录 `.gitignore` 后提交即可。

---

## 7. 常见问题

- **推送被拒绝（远端已有历史）**：说明远端已有提交，执行
  `git pull --rebase origin main` 后再 `git push`。
- **要求输入账号密码**：确认已 `gh auth login` 并执行 `gh auth setup-git`。
- **误提交了被过滤文件**：若已提交但尚未推送，可 `git rm -r --cached <路径>` 后重新提交；
  若已推送，需重写历史（谨慎操作）。
