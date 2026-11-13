# Windows Server 2022 原生部署

适合目前这台4核/4GB、无公网IP的服务器。先在服务器自己的浏览器访问 `https://localhost`，不安装Docker，不安装服务器端Node，不开放网络端口。前端在开发电脑构建并打包；服务器用Python运行后端、Caddy提供HTTPS与静态页面、WinSW管理两个Windows服务。本文所有服务器命令均在**管理员PowerShell**中运行。

现有可用内存约0.2GB时先不要安装和启动。查看任务管理器，关闭确认不需要的软件，确认系统页面文件启用；配置脚本在可用内存低于768MB或运行目录所在磁盘可用空间不足6GB时会停止。此检查只是安装下限，不保证检索负载足够，首次加载向量模型仍可能增加数百MB乃至更多内存。页面文件能缓解提交内存不足，不能替代物理内存，也不会让检索更快。先测试单用户，不在这台机器运行Ollama或其他聊天大模型。

## 1. 开发电脑生成发布包

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

脚本会创建项目内 `.venv`、先安装CPU版PyTorch，再安装后端依赖。下载进度直接显示在当前窗口；实际大小和耗时取决于源与依赖，若失败先修复网络再重新执行。配置已经生成时不会覆盖 `server.env`。版本更新后如需更新依赖，也可停止服务后重新执行此命令。

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

由于没有公网域名，使用Caddy本地CA签发HTTPS证书，仍保持Secure Cookie与生产网络限制。启动网关后找到本机CA的**公开证书**：

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
& .\.venv\Scripts\python.exe .\deploy\windows\runtime.py --env-file C:\ChatFoundryRuntime\server.env backup --output C:\ChatFoundryBackups\before-update.zip
```

确认备份成功后，再将新发布包内容覆盖到 `C:\ChatFoundry`，保留原 `.venv` 与完整的外部运行目录。包里不包含 `server.env`，不会替换账号和密钥。备份包含个人数据和文件形式主密钥，环境配置中另存的Key须独立安全保管，并将备份复制到其他机器，避免单盘故障。

更新依赖/服务配置，再检查并升级数据库：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -PythonExe C:\Python310\python.exe -InstallDependencies
& .\.venv\Scripts\python.exe .\deploy\windows\runtime.py --env-file C:\ChatFoundryRuntime\server.env upgrade
# 上一步必须成功，失败时保持停机并检查原因。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Manage-Services.ps1 -Action Start
```

服务名和代码路径不变时不需要重新Install；修改环境配置后Restart即可，每次后端进程启动都会重读独立环境文件。修改服务可执行路径或切换运行目录时，停止并Uninstall旧服务，再用新配置Install；Uninstall保留源代码和用户数据。

恢复只允许写入**不存在的新目录**：

```powershell
& .\.venv\Scripts\python.exe .\deploy\windows\runtime.py --env-file C:\ChatFoundryRuntime\server.env restore --archive C:\ChatFoundryBackups\before-update.zip --destination C:\ChatFoundryRuntime\restored-data
```

首次从开发电脑带旧数据时，先停止开发后端，用原项目的全量备份工具导出，并在服务器创建新账号之前完成恢复。先确认备份文件校验成功；停服务，将 `server.env` 的 `DATA_DIR` 指向恢复目录，并授予服务读写权限：

```powershell
icacls.exe C:\ChatFoundryRuntime\restored-data /grant '*S-1-5-19:(OI)(CI)M' /T
```

如数据来自不同代码版本，先运行upgrade，成功后再启动。验证原账号、历史会话、引用、知识库和Key都正常后再继续使用。回退数据库迁移需要同时恢复匹配的旧代码与完整备份，不能只换旧代码。

## 后续开放内网访问

目前默认只允许服务器本机访问，你的电脑直接打开服务器IP不会连上，这是预期行为。确定通过同一VPC、VPN或公网代理访问后，再调整入口；这一步不会凭空为服务器增加公网IP。

同一内网/VPN测试时，先停止服务，再执行配置脚本并指定真实内网IP与监听地址，例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Configure.ps1 -SiteHost 10.1.0.13 -ListenAddress 0.0.0.0
```

然后只向指定客户端IP/网段开放Windows防火墙与云安全组的443端口，客户端导入这台服务的CA公开证书，再访问 `https://10.1.0.13`。后端8000始终不对外开放。配置改动后Restart服务；如果改过内网监听配置，今后重新运行Configure时继续传相同的SiteHost/ListenAddress，避免恢复成本机默认。
