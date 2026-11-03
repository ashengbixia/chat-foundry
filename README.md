# chat-foundry

基于 Vue 3 和 FastAPI 的 AI 聊天学习项目。

## 环境与依赖安装

当前后端开发环境为 Python 3.10.20。建议使用独立的 Python 3.10 虚拟环境，不要复用装有其他项目依赖的大环境。
前端需要 Node.js `^22.18.0 || >=24.12.0`，与 `frontend/package.json` 中的要求一致；可选择满足要求的 Node 22 或 Node 24 版本。

以下命令使用 Windows CMD，从下载后的项目根目录执行，不要求文件夹名称与 GitHub 仓库名一致。

### 安装后端依赖

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

先确认这里的 `python` 是准备使用的 Python 3.10。清单固定了当前项目使用的直接依赖及配置读取依赖，间接依赖由 pip 解析，不是当前 Conda 环境的全部包列表。

### 安装前端依赖

```bat
cd frontend
npm ci
```

`npm ci` 根据已提交的 `package-lock.json` 安装，不需要提交 `node_modules`。

## 后端模型配置

模型配置统一由 `backend/app/core/config.py` 读取，密钥不要写进源码、前端或 HTTP 测试文件。

可选择以下任一方式：

1. 在 PyCharm 的后端运行配置中设置环境变量 `LLM_BASE_URL`、`DEEPSEEK_API_KEY`、`LLM_MODEL`、`LLM_TIMEOUT`。
2. 在启动后端的终端设置这些环境变量，然后在同一终端启动后端。
3. 本机开发时，将 `backend/.env.example` 复制为 `backend/.env`，填写真实配置。

环境变量优先于 `.env`，未提供的配置使用代码默认值。配置在进程启动时读取，修改后需要重启后端。

密钥统一使用 `DEEPSEEK_API_KEY`，不再读取旧名称 `LLM_API_KEY`；如果本机 `.env` 仍使用旧名称，请改名，其他配置保持不变。

`LLM_BASE_URL` 是聊天补全 API 的基础地址，程序会追加 `/chat/completions`；服务要求 `/v1` 时，应将它包含在基础地址中。

例如使用 DeepSeek 时，可在项目根目录的 CMD 终端设置以下进程环境变量。密钥必须替换为自己的值，不要把真实值写进本文档或提交到 Git：

```bat
set "DEEPSEEK_API_KEY=替换为自己的密钥"
set "LLM_BASE_URL=https://api.deepseek.com"
set "LLM_MODEL=deepseek-chat"
set "LLM_TIMEOUT=60"
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

这些 `set` 命令只对当前 CMD 及其启动的子进程生效。使用 PyCharm、PowerShell 或新的终端时，请在对应运行环境中配置，不能假设它们自动共享刚才的进程变量。

`.env` 是本机明文配置文件，不是加密存储；环境变量也不是加密保险箱。不要分享配置截图、打印密钥或将密钥放在 `VITE_` 前缀的前端变量中。

## 启动和验证

后端保持运行，另外打开一个终端，从项目根目录启动前端：

```bat
cd frontend
npm run dev
```

前端默认代理到 `http://127.0.0.1:8000`，打开 Vite 终端显示的页面地址。后端健康检查为 `http://127.0.0.1:8000/health`，接口文档为 `http://127.0.0.1:8000/docs`。

没有数据库时，正常启动后端会创建 `backend/data/app.db` 和当前模型声明的表，得到空的聊天数据。不能恢复原作者的会话和上传资料。

当前迁移文件是针对早期已有表增加消息序号的增量脚本，不是完整的从零建库迁移链。首次使用请按上述应用启动流程建库，不要先运行 `alembic upgrade head`；已有数据库也不要在未确认版本和备份的情况下盲目迁移。后续仍需整理迁移基线。

本清单来自当前开发环境及源码依赖核对，尚未完成全新环境安装和端到端验收；它不是跨平台完全锁定或生产部署安全审核的承诺。

## Git 提交注意事项

根目录 `.gitignore` 已排除 `.env`、运行数据库及备份、聊天数据、上传文件、依赖目录和本机 IDE 配置。
只提交不含真实密钥的 `.env.example`，数据库迁移脚本可以正常提交。

忽略规则不会自动移除已被 Git 跟踪的文件；如果密钥曾被提交或公开，需要撤销并重新生成密钥，仅删除文件或改用环境变量不足以消除泄露。
