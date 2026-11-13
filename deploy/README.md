# 单机多人部署

Windows Server 2022、不使用Docker的部署见 [Windows原生部署](windows/README.md)，支持先在服务器本机通过HTTPS测试。

开发启动完全保留：PyCharm运行 `python -m uvicorn backend.app.main:app --reload`，前端运行 `npm run dev`。开发默认HTTP、本机Ollama可访问；这些生产文件不会自动改变本机配置。开发与生产应使用各自的数据目录。

本方案为单机、单后端进程，使用 SQLite、Chroma 和持久卷。不要设置多个 Uvicorn workers、不要横向扩容；跨进程数据锁会拒绝第二个后端访问同一目录。模型/索引限额在进程内，跨机器部署需要另行引入共享队列和数据库。当前没有生产负载测试结论，建议先小范围试用并观测内存、磁盘、延迟和失败率。

## 首次启动

1. 安装Docker Engine与Compose插件，准备真实域名，将DNS指向服务器，开放80/443端口。此方案由Caddy自动签发证书，后端8000端口不发布到宿主机。
2. 在项目根目录复制 `deploy/.env.example` 为 `deploy/.env`，填写 `APP_DOMAIN` 和随机长 `BOOTSTRAP_TOKEN`。初始管理员注册页需要输入这个口令，避免部署期间被他人抢先注册。不要将此文件提交Git。
3. 模型可以由每个用户登录后配置；也可以填写服务器的 `LLM_BASE_URL`、`LLM_MODEL`、`DEEPSEEK_API_KEY` 提供默认服务。首次运行需要下载向量模型，可使用持久缓存；离线部署需提前准备同型号缓存/本地模型目录。
4. 执行 `docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build`。访问 `https://你的域名`，输入初始化口令创建首位管理员。然后清空 `.env` 的 `BOOTSTRAP_TOKEN`，执行 `docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --force-recreate backend`，确保容器重建并读取新环境变量，不需要重新构建镜像。
5. 默认关闭公开注册。管理员左下角“用户管理”可以创建普通账号；需要开放自助注册时将 `REGISTRATION_ENABLED=true` 并重新创建容器。新管理员仍须申请并获现有管理员批准。

后端健康检查为 `/health`。查看日志：`docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=100 backend`。仅网关固定IP可提供可信转发头；若更改Compose子网，必须同时修改Dockerfile中的 `--forwarded-allow-ips`。如子网与现有网络冲突，先修改再启动。公网访问必须保持HTTPS与 `AUTH_COOKIE_SECURE=true`。

## 本地/内网模型

生产环境用户自定义模型默认只允许公网标准HTTPS，DNS的所有地址都必须是公网。每次请求重新校验、连接已验证的IP并保留TLS SNI，禁止重定向，不读取服务器代理环境变量。模型发现、连接测试、正式聊天、标题和子任务复用此出口。

内部模型由部署管理员在 `MODEL_INTERNAL_URLS` 填写JSON数组，例如 `["http://model-service:11434/v1"]`。用户仍需在页面填同一个基础地址；这里授权完整API基础地址，不支持网段或通配符。授权后所有已登录用户都可以请求该服务，应只添加专门的模型API，并在网络层隔离其他内部管理服务。

Docker中的 `127.0.0.1` 是后端容器，不是宿主机；宿主机模型需使用可达地址，Linux可自行配置host-gateway映射，远程模型须设置防火墙。服务的Key仍按每个用户加密保存；授权内网地址不会自动分享Key。开发环境保持允许本机Ollama。

## 限额与存储

- 模型调用入口默认全局8个、每用户3个，等待30秒；推理请求、模型列表与测试、自动标题共用限额，Ollama元数据批次内另限制4个并发。排队超时可重试。等待子任务期间不持有推理槽，取消或断连释放资源。前台完整回答另有全局32个、每用户6个限额，超额返回429；子任务仍受原有子任务限额与模型请求限额约束。生产Uvicorn另限制100个并发连接，达到限制会返回503。
- 索引同时计算1个，最多预约100个任务，排队状态保存在数据库。服务重启后未完成任务显示中断，需要重建。
- 每用户默认100MiB原始文件、200个文件，全站默认10GiB原始文件；同账号同内容复用不重复计费。写锁内确认额度，防止并发上传突破额度。单文件仍为5MiB。额度不包含数据库里的正文/快照和向量索引，因此必须额外监控卷的实际磁盘使用量。
- 网关限制请求体8MB；后端容器限制4GB内存、2CPU和256进程。依据实际模型/浏览器读取负载调整。生产镜像默认静态网页读取；若开启Playwright，需自行安装匹配浏览器及系统依赖。
- `app_data` 保存数据库、上传原文、向量库和本地加密主密钥；`model_cache` 保存向量模型缓存；Caddy卷保存证书。`docker compose down`不会删除命名卷，**不要执行 `down -v`**。

## 从现有本机数据迁移

先停止本机后端，再用下方备份命令导出整个数据目录。恢复到新目录验证后，可挂载到服务器 `/data`，或复制到新建且未运行的 `app_data` 卷，并将所有者设为容器UID/GID `10001:10001`。不要仅复制app.db，否则会丢失原文件、向量和Key主密钥。不要把生产空卷直接当成原本机数据。

## 后续更新与配置生效

以下命令均在项目根目录执行；如果Docker需要管理员权限，在每条Docker命令前加 `sudo`。不要切到另一个Compose项目名或部署目录而误用新卷；更新应沿用原项目目录、Compose项目名和数据卷。

普通代码更新（版本说明未要求数据库迁移）：

```sh
git pull
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
```

只修改 `deploy/.env`（例如注册开关、初始化口令或模型授权）时，不需要重新构建镜像：

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --force-recreate
```

`restart` 不会重新读取容器环境变量。新增配置项也需要手动补入服务器的 `.env`，不要用示例文件覆盖已有密钥。

涉及数据库迁移的版本按以下顺序操作：先拉取代码并构建新镜像，再停止后端、完成下节的全量备份，然后运行新镜像里的迁移程序，成功后启动。仅 `git pull` 不会更新已有镜像；直接运行迁移可能执行旧版本程序。迁移失败时先检查原因，不要继续启动或直接回退代码。

```sh
git pull
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml stop backend
# 此处必须先按下节完成全量备份，确认成功后再继续迁移。
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps backend python -m backend.app.core.db_setup --upgrade
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

旧数据库版本落后时，后端会拒绝启动；反复执行 `up --build` 不能替代迁移。全新空数据目录会自动初始化，无需手动升级。

## 离线备份与恢复

必须先停止后端。备份与后端共用数据目录锁，后端运行时拒绝备份。备份包含SQLite一致性快照、原文件、Chroma索引和文件形式主密钥，带逐文件SHA256清单；恢复检查清单和数据库完整性，**只写入不存在的新目录**，拒绝覆盖与路径穿越。归档不加密，应放在受限目录并对异地副本加密；若主密钥使用 `MODEL_CONFIG_ENCRYPTION_KEY` 环境变量，必须另行安全备份环境配置。

本机项目根目录示例（替换为自己的实际路径）：

```sh
python -m backend.app.core.backup backup --data-dir E:/chatfoundry-data --output E:/backups/chatfoundry.zip
python -m backend.app.core.backup restore --archive E:/backups/chatfoundry.zip --destination E:/chatfoundry-restored
```

Docker备份示例，先创建项目根目录 `backups` 并使UID10001可写：

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml stop backend
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps -v ./backups:/backups backend python -m backend.app.core.backup backup --data-dir /data --output /backups/chatfoundry.zip
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

恢复先在本机或维护容器的**新目录**验证；确认会话、附件溯源、知识库检索和个人Key可正常使用后再停机切换挂载。环境变量中的Key、域名和证书卷需独立保管。保留旧卷和旧镜像，回滚时停机切回匹配的旧数据备份与镜像，不能只降代码而保留已升级数据库。

## 上线前验收

用两个普通账号验证彼此会话/附件/知识库不可见；同时生成、切换会话和取消一轮，确认另一轮继续；验证关闭注册、Cookie带Secure、用户内网地址被拒绝而授权模型可用；上传并检索资料后重启确认持久化；在临时新目录做一次备份恢复演练，再验证历史引用和个人Key。用真实预计并发量持续试用，观察模型等待超时、SQLite锁等待、索引排队、磁盘和内存后再扩大用户范围。

## 首次部署常见问题

- 固定子网为 `172.30.204.0/24`，网关为 `172.30.204.2`。部署前确认不与服务器现有Docker网络或路由冲突；若修改子网，必须同步修改Compose中的两个服务IP以及Dockerfile中的 `--forwarded-allow-ips`，然后重新构建后端镜像。否则HTTPS转发信息可能不被信任，同源校验可能拒绝登录操作。
- 基础镜像、npm、pip依赖和首次向量模型下载都需要可达的下载源。Docker镜像拉取由宿主机Docker配置决定；npm/pip构建下载源需要在构建阶段配置，在运行环境 `.env` 填写下载源不会影响已经进行的镜像构建。Hugging Face可通过 `HF_ENDPOINT` 选择你确认可信且可达的兼容源；第三方镜像可能存在可用性或内容差异。
- 首次后端启动可能需要较长时间。健康检查 `start_period=600s` 是失败宽限期，并非强制等待10分钟；后端一旦健康网关就能启动。首次网关尚未启动时通常是连接失败，已有网关等待后端恢复时才可能出现502。用 `docker compose --env-file deploy/.env -f deploy/compose.yaml ps` 和后端日志判断状态。
- `/docs` 未被网关转发到后端，会进入前端页面；这不等于后端故障，健康检查请使用 `/health`。
- 仓库内的本文路径是 `deploy/README.md`，本地开发机绝对路径不适用于其他机器。若使用压缩包而非Git克隆，目录没有Git历史时不能直接 `git pull`；更新源码时保留服务器 `.env`、原Compose项目名及持久卷。
