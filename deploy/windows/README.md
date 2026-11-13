# Windows Server 2022 原生部署

本文介绍Windows Server 2022的原生部署方式，无需Docker。前端在开发电脑构建并打包，服务器用Python运行后端、Caddy提供HTTPS与静态页面、WinSW管理后台服务。默认先通过服务器本机的 `https://localhost` 验证部署，再按需启用内网或公网访问。本文所有服务器安装与服务管理命令均在**管理员PowerShell**中运行，明确标注的预览命令除外。

安装前检查可用内存和磁盘空间。配置脚本在可用内存低于768MB或运行目录所在磁盘可用空间不足6GB时会停止；此检查只是安装下限，不保证实际检索负载所需的容量。首次加载向量模型会增加内存占用，应按文档大小和并发量实测。资源紧张时可关闭不需要的软件、降低并发，并检查系统页面文件；页面文件不能替代物理内存。本文默认聊天通过外部模型API提供，本地运行聊天大模型需另行评估资源。

文中的 `C:\ChatFoundry`、`C:\ChatFoundryRuntime` 和 `C:\Python310` 是示例安装路径，域名与内网IP也是示例，部署时请替换为自己的实际配置。

## 1. 获取部署文件

### 方式A：下载已构建的Windows发布包（推荐）

打开项目的 [GitHub Releases](https://github.com/ashengbixia/chat-foundry/releases)，下载对应版本的 `ChatFoundry-*.zip` 部署包。它包含 `backend`、`frontend/dist` 和 `deploy/windows`，不包含Python依赖、Caddy、WinSW和用户数据。GitHub自动提供的 `Source code (zip)` / `Source code (tar.gz)` 是源码压缩包，不包含前端构建产物，不能当作这个部署包使用。

将部署包解压到 `C:\ChatFoundry`，确认存在以下文件，然后直接从本文第2节继续；无需安装Node、执行npm或再生成一次发布包：

```text
C:\ChatFoundry\backend\requirements.txt
C:\ChatFoundry\frontend\dist\index.html
C:\ChatFoundry\deploy\windows\Configure.ps1
```

发布包解压后这三个目录直接位于根下，不要额外套一层目录。本文的Windows部署包不包含Docker构建所需的完整前端源码与Compose文件；Docker部署请下载对应版本的完整源码，并按仓库中的 [Docker部署说明](https://github.com/ashengbixia/chat-foundry/blob/master/deploy/README.md) 执行。

### 方式B：从源码自行构建

仅在需要部署尚未发布的源码版本或自行修改代码时使用此方式。先在开发电脑下载完整源码，安装项目根README指定的Node版本与npm，再执行以下命令；不要在仅包含dist的Windows部署包里运行构建脚本。

在项目根目录PowerShell执行（本机需要原有Node/npm）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Build-Package.ps1
```

生成 `release\ChatFoundry-时间-随机值.zip`，包含后端源码、迁移文件、构建后的前端和Windows部署脚本，排除 `.env`、本地数据库/上传文件、Python环境和开发依赖。前端已自行构建时可以使用 `-SkipBuild`。外部工具和Python依赖不在包内。

将ZIP上传到服务器并解压，使项目根目录为 `C:\ChatFoundry`，例如应存在 `C:\ChatFoundry\backend\requirements.txt`，不要额外套一层目录。发布包无需Git；以后更新用新包覆盖代码文件，保留 `.venv`，数据放在代码目录外。不要将开发电脑的整个Python环境复制到服务器。

## 2. 服务器准备Python与独立配置

从[Python官方](https://www.python.org/downloads/windows/)安装64位Python 3.10，选择“为所有用户安装”，建议路径 `C:\Python310`，避免服务无法访问个人用户目录中的Python。若已安装，使用已有3.10路径。

安装[Microsoft Visual C++ x64运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)，用于PyTorch等原生依赖；安装器要求重启时先完成重启。

```powershell
Set-Location C:\ChatFoundry
Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory, TotalVisibleMemorySize
Get-PSDrive C
& C:\Python310\python.exe --version

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -PythonExe C:\Python310\python.exe -InstallDependencies
```

脚本会创建项目内 `.venv`、先安装CPU版PyTorch，再安装后端依赖。下载进度直接显示在当前窗口；实际大小和耗时取决于源与依赖，若失败先修复网络再重新执行。配置已经生成时不会覆盖 `server.env`。`-InstallDependencies` 与虚拟环境是否已存在是两个独立判断：只要传入该参数就会执行pip安装流程；依赖已经完整安装且无需更新时，应去掉该参数，只运行配置检查与生成。

依赖下载源由pip配置或安装命令决定；向量模型下载入口可按网络条件在实际 `server.env` 中配置 `HF_ENDPOINT`。选择自己信任的兼容服务，不强制使用第三方镜像。修改模板不会更新已生成的server.env，修改实际配置后需要重启服务。Windows文件扩展名可能默认隐藏，使用记事本保存配置时应保留文件名server.env，不要另存为server.env.txt。

独立运行目录默认 `C:\ChatFoundryRuntime`：

| 内容 | 位置 |
|---|---|
| 私有生产配置/初始化口令 | `server.env` |
| 数据库、原文件、向量及个人Key主密钥 | `data` |
| 向量模型下载缓存 | `cache\huggingface` |
| HTTPS证书和私有CA | `caddy` |
| 可轮转服务日志 | `logs` |
| Windows服务配置和包装器 | `services` |
| Caddy/WinSW工具 | `bin` |
| 网关域名、监听地址、证书模式 | `gateway.json` |

配置脚本自动生成随机初始化口令，不在终端打印。用管理员记事本打开 `C:\ChatFoundryRuntime\server.env` 查看 `BOOTSTRAP_TOKEN`。模型地址、模型名和Key先留空，创建管理员后在网页里配置个人模型服务。生产配置与开发用的 `backend/.env` 分离，服务器不会误用开发配置。

小内存默认：模型请求全局2个/每用户1个、前台会话全局4个/每用户2个、索引1个、CPU线程1个，浏览器渲染默认不启用。`EMBEDDING_PRELOAD=false` 使聊天服务启动时不加载向量模型；首次上传索引/检索时才下载和加载，后续仍会占用内存。普通聊天需要外部模型API；本机没有大模型也能聊天。

## 3. 放入Caddy和WinSW，安装后台服务

下载以下官方Windows x64工具，上传到 `C:\ChatFoundryRuntime\bin`（外部二进制不放进Git）：

- [Caddy官方](https://caddyserver.com/download)：选择Windows amd64，将程序命名为 `caddy.exe`。
- [WinSW 2.12.0官方发布](https://github.com/winsw/winsw/releases/tag/v2.12.0)：下载 `WinSW-x64.exe`。服务XML按此版本编写。

确认工具来源与发布校验信息后，在项目根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Manage-Services.ps1 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Manage-Services.ps1 -Action Start
Get-Service ChatFoundryBackend, ChatFoundryGateway
```

服务使用权限受限的 `NT AUTHORITY\LocalService`，自动启动、失败重启，后台运行不弹窗口。后端仅监听 `127.0.0.1:8000`，网关默认仅监听本机，后端只信任本机网关的转发头。无需 `--reload`。配置脚本不会自动放行防火墙，也不会删除任何数据。

若服务显示Running但页面暂时502，先查看后端是否正在初始化；服务状态不等于应用健康：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Get-ChildItem C:\ChatFoundryRuntime\logs
# 根据上面显示的实际文件名查看日志：
Get-Content C:\ChatFoundryRuntime\logs\ChatFoundryBackend.out.log -Tail 80 -Wait
```

错误通常记录在对应 `.err.log`；WinSW自身信息也在logs目录。日志有大小轮转，不要将日志中的私人聊天内容公开。

## 4. 信任本机HTTPS证书，创建管理员

默认Internal模式使用Caddy本地CA签发HTTPS证书，仍保持Secure Cookie与生产网络限制。启动网关后找到本机CA的**公开证书**；已经按公网章节启用Public模式时无需导入本地CA：

```powershell
Get-ChildItem C:\ChatFoundryRuntime\caddy -Filter root.crt -Recurse
```

默认路径通常为 `C:\ChatFoundryRuntime\caddy\data\caddy\pki\authorities\local\root.crt`。确认这是自己这次部署生成的CA，再导入该文件以信任这套服务：

```powershell
certutil.exe -addstore -f Root C:\ChatFoundryRuntime\caddy\data\caddy\pki\authorities\local\root.crt
```

关闭并重新打开服务器浏览器，访问 **`https://localhost`**，不要使用HTTP后端地址登录。填写 `server.env` 的初始化口令创建管理员。完成后清空其中的 `BOOTSTRAP_TOKEN`，然后：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Manage-Services.ps1 -Action Restart
```

登录后配置模型服务，测试普通问答，再上传一个小TXT验证索引、检索和引用。模型首次下载/索引可能明显慢于普通聊天；观察任务管理器内存与磁盘。切勿分享CA私钥、`server.env` 或整个运行目录。

## 5. 后续更新、迁移和备份

**先备份，再更新代码，涉及数据库变化再迁移。** 同一管理员PowerShell中，先停服务，然后用旧代码备份：

```powershell
Set-Location C:\ChatFoundry
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Manage-Services.ps1 -Action Stop
# 自行选择有足够空间的备份位置，每次使用新文件名；不要放在data目录内部。
New-Item -ItemType Directory C:\ChatFoundryBackups -Force
$backupFile = 'C:\ChatFoundryBackups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip'
& .\.venv\Scripts\python.exe .\deploy\windows\runtime.py --env-file C:\ChatFoundryRuntime\server.env backup --output $backupFile
if ($LASTEXITCODE -ne 0) { throw 'Backup failed. Do not overwrite the current deployment.' }
```

确认备份成功后，再将新发布包内容合并覆盖到 `C:\ChatFoundry`，保留原 `.venv` 与完整的外部运行目录。不要先删除整个部署目录，也不要用不含dist的源码压缩包替换部署包。包里不包含 `server.env`，不会替换账号和密钥。备份命令只备份DATA_DIR，其中包含个人数据和文件形式主密钥；还需单独安全备份server.env、gateway.json和caddy目录，并将备份复制到其他机器，避免单盘故障。

根据版本说明选择需要的更新步骤：只更新前端时，用完整部署包中的dist替换原dist即可；仅后端业务代码变化不要求重新构建前端。只有依赖清单或依赖版本要求变化时才在Configure命令中加入 `-InstallDependencies`；数据库迁移变化时才需要执行upgrade。以下为需要重新生成部署配置的示例，不会重复安装依赖：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -PythonExe C:\Python310\python.exe
if ($LASTEXITCODE -ne 0) { throw 'Configuration failed. Keep services stopped and inspect the error.' }
```

若版本说明要求数据库迁移，在启动服务之前执行；迁移失败时保持停机并检查原因：

```powershell
& .\.venv\Scripts\python.exe .\deploy\windows\runtime.py --env-file C:\ChatFoundryRuntime\server.env upgrade
if ($LASTEXITCODE -ne 0) { throw 'Database upgrade failed. Do not start services.' }
```

确认上述需要的步骤都成功后启动，并重新检查后端健康、前端页面、登录与检索：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Manage-Services.ps1 -Action Start
Invoke-RestMethod http://127.0.0.1:8000/health
```

服务名和代码路径不变时不需要重新Install；修改环境配置后Restart即可，每次后端进程启动都会重读独立环境文件。网关设置保存到 `gateway.json`，再次运行Configure时，未明确传入的参数沿用已有配置，不会把公网域名恢复为localhost。旧版没有gateway.json时会识别原脚本生成的Caddyfile，保留原主机名与监听地址；无法识别的自定义配置会停止并提示，不静默覆盖。修改服务可执行路径或切换运行目录时，停止并Uninstall旧服务，再用新配置Install；Uninstall保留源代码和用户数据。

恢复只允许写入**不存在的新目录**：

```powershell
& .\.venv\Scripts\python.exe .\deploy\windows\runtime.py --env-file C:\ChatFoundryRuntime\server.env restore --archive C:\ChatFoundryBackups\20260101-120000.zip --destination C:\ChatFoundryRuntime\restored-data
```

上面的归档名仅为示例，替换为实际已成功生成的备份文件。

首次从开发电脑带旧数据时，先停止开发后端，用原项目的全量备份工具导出，并在服务器创建新账号之前完成恢复。先确认备份文件校验成功；停服务，将 `server.env` 的 `DATA_DIR` 指向恢复目录，并授予服务读写权限：

```powershell
icacls.exe C:\ChatFoundryRuntime\restored-data /grant '*S-1-5-19:(OI)(CI)M' /T
```

如数据来自不同代码版本，先运行upgrade，成功后再启动。验证原账号、历史会话、引用、知识库和Key都正常后再继续使用。回退数据库迁移需要同时恢复匹配的旧代码与完整备份，不能只换旧代码。

## 后续开放内网访问

目前默认只允许服务器本机访问，你的电脑直接打开服务器IP不会连上，这是预期行为。确定通过同一VPC、VPN或公网代理访问后，再调整入口；这一步不会凭空为服务器增加公网IP。

同一内网/VPN测试时，先停止服务，再执行配置脚本并指定真实内网IP与监听地址。以下 `192.168.1.100` 仅为示例，必须替换为服务器实际内网IP：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -SiteHost 192.168.1.100 -ListenAddress 0.0.0.0
```

如果此前已启用公网模式，切回内网时额外传 `-TlsMode Internal`。然后只向指定客户端IP/网段开放Windows防火墙与云安全组的443端口，客户端导入服务的CA公开证书，再访问实际内网地址（示例：`https://192.168.1.100`）。后端8000始终不对外开放。配置改动后Restart服务；以后更新时不必重复传SiteHost/ListenAddress，脚本会沿用保存的值。

## 公网域名 HTTPS

代码提供入口配置，不会购买公网IP、修改DNS、自动放行安全组或代办备案。此模式适用于公网流量直接转发到Caddy，由Caddy终止HTTPS；前置负载均衡已经终止HTTPS的场景需要另行匹配代理方案。

### 配置前先确认

1. 服务器具备公网IP，或已有将公网TCP 80/443转发到这台服务器的入口。只有 `10.x.x.x` 内网IP不够。
2. 域名A记录解析到该公网入口。没有可用IPv6链路时不要设置AAAA记录；当前监听选项为IPv4。
3. Windows防火墙、云安全组和任何中间转发均允许TCP 80/443；其他站点未占用这两个端口。后端8000不开放。
4. 按服务器所在地和实际服务要求完成相应域名实名、备案等流程。

### 从已部署的本机版本切换

先更新 `deploy/windows` 中的脚本（包括新增的 `Gateway.ps1`），在管理员PowerShell里进入项目根目录。把下面的示例域名换成自己的域名；不需要重新安装Python依赖，也不需要重新注册已有服务：

```powershell
Set-Location C:\ChatFoundry
$ms = '.\deploy\windows\Manage-Services.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ms -Action Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -TlsMode Public -SiteHost chat.example.com
# 必须确认配置脚本成功，再启动服务。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ms -Action Start
```

首次切换到Public且没有明确指定ListenAddress时，自动使用 `0.0.0.0`。公网模式仅接受DNS域名，拒绝localhost、IP和本地域名。可选参数 `-AcmeEmail admin@example.com` 提供证书联系邮箱，不是网站用户注册邮箱；不填也可以申请证书。生产Cookie仍为Secure，后端继续只监听127.0.0.1并信任本机Caddy。

生成的配置不再使用 `tls internal`，Caddy启动后会自动申请公开可信证书，并将HTTP重定向至HTTPS，后续自动续期。访问 `https://你的域名`，用户不需要导入本地CA。首次签发取决于域名解析和公网可达性，配置校验成功不等于证书已经签发。

查看状态与签发日志：

```powershell
Get-Service ChatFoundryBackend, ChatFoundryGateway
Invoke-RestMethod http://127.0.0.1:8000/health
Get-Content C:\ChatFoundryRuntime\logs\ChatFoundryGateway.err.log -Tail 80
```

若公网网页不通但本机后端健康，依次核对DNS、公网入口、80/443占用与防火墙，以及网关日志中的ACME错误。不要反复更改域名或清空证书目录尝试签发，避免触发证书机构限额。

### 预览与更新保留

`-Preview` 仅显示候选Caddy配置，不安装依赖、不写配置、不停启服务；可在正式切换前预览，无需管理员权限：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -Preview -TlsMode Public -SiteHost chat.example.com
```

正式配置写入 `C:\ChatFoundryRuntime\gateway.json`，现有Caddyfile会备份为 `Caddyfile.before-configure`。Caddy已安装时先校验候选文件，失败保留原Caddyfile和网关设置。以后更新代码时，停止服务后直接运行Configure（按需加InstallDependencies）即可沿用保存的域名、监听地址和证书模式。不要覆盖或删除整个运行目录；备份时还要单独保管server.env、gateway.json和caddy证书目录，数据备份命令本身只备份DATA_DIR。

若要恢复为仅本机访问，停止服务后明确执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -TlsMode Internal -SiteHost localhost -ListenAddress 127.0.0.1
```

确认成功后启动服务，并按本机部署章节信任本地CA。服务不会自动增删Windows防火墙规则。
