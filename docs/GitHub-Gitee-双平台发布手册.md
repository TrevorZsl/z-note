# GitHub 与 Gitee 双平台发布手册

这份手册供维护者和其他 Agent 接手发布。推送一个版本标签，构建一次，把同一份安装包和校验文件上传到 GitHub 草稿与 Gitee 预览版。两个平台验收通过后，由维护者确认正式发布。

## 交给 Agent 的任务说明

> 按本手册为 `<项目>` 准备 `<版本号>` 的双平台发布。先检查现有工作流、Git 差异、版本号和远端标签。用同一次构建生成安装包和 `SHA256SUMS`，将两份文件上传到 GitHub 草稿和 Gitee 预览版。任一侧失败时保留成功的一侧，从原构建产物补传。核对两边下载到的文件与原构建哈希一致后，交付两个待发布页面。正式发布由维护者确认。

交接时提供源码仓、两个发布仓、目标版本、发布分支和现有打包入口。有现成工作流就沿用它。移植到其他项目时，替换仓库路径、版本号来源、安装包名称、测试命令和打包命令。令牌由维护者配置为 Actions Secrets。

## 仓库和凭据

| 用途 | z-note 实例 | 移植时替换 |
| --- | --- | --- |
| 私有源码仓与 Actions | `TrevorZsl/z-note` | 源码仓，承载工作流和构建产物 |
| GitHub 发布仓 | `TrevorZsl/z-note-releases` | 公开仓，初始化默认分支 |
| Gitee 发布仓 | `trevorzsl/z-note-releases` | 公开仓，初始化默认分支 |
| 版本与说明 | `version.py`、`releases/<版本>.md` | 版本来源与公开更新说明 |
| 打包入口 | `scripts/package.ps1 -Installer` | 项目自己的打包命令 |
| 工作流 | `.github/workflows/release.yml` | 双平台发布实现参考 |

两个公开发布仓仅承载公开说明和安装附件。GitHub 的 `RELEASE_TOKEN` 使用仅能写入目标发布仓的细粒度令牌，仓库 `Contents` 权限为读写。Gitee 的 `GITEE_TOKEN` 使用对目标发布仓有写权限的私人令牌。两者都放在**源码仓**的 **Settings → Secrets and variables → Actions**。源码仓自带的 `GITHUB_TOKEN` 用于读取该仓的 Actions 构建产物。上传令牌只注入对应的上传步骤，保持在 Secrets 中。 [GitHub Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)

## 配置工作流

1. **构建任务**监听 `v*.*.*` 标签。确认标签和程序版本一致，确认公开说明存在，再运行项目测试。z-note 用 PyInstaller 打包、Inno Setup 生成安装包。打包后按 `<64 位小写 SHA-256><两个空格><文件名>` 写入 `SHA256SUMS`。将安装包和校验文件保存为同一个 Actions Artifact，例如 `z-note-2.1.8`。
2. **GitHub 上传任务**下载这个 Artifact，用 `gh release create --draft` 把两份附件和更新说明传到公开发布仓。草稿上传失败时，先核对已有草稿和附件，再用同一次构建的文件补齐。
3. **Gitee 上传任务**下载同一个 Artifact，校验本地文件后，通过 Gitee v5 API 查找目标标签。缺少发行版时创建 `prerelease: true` 的预览版，目标提交选择**发布仓**的默认分支。按 `SHA256SUMS`、安装包的顺序上传附件。先校验同名已有附件的大小和 SHA-256，匹配就复用，缺少就补传，冲突就停下来检查。Gitee 预览版在公开仓可见，z-note 更新器会过滤它。
4. **仅补传 Gitee 的入口**使用 `workflow_dispatch`。输入 `source_run` 为原标签触发的构建运行编号，可选输入 `gitee_runner` 指定一个已在线的 Runner 标签。校验原运行来自同一源码仓、同一构建工作流及版本标签，并确认 Artifact 仍存在。补传只执行 Gitee 任务，原安装包和 GitHub 草稿继续使用。

Gitee 附件接口是 `POST https://gitee.com/api/v5/repos/<owner>/<repo>/releases/<release_id>/attach_files`，实测可用的请求是 `multipart/form-data`，表单字段为 `access_token` 和 `file`。检查 HTTP 状态、返回的文件名和字节数。日志保留状态码、已发送字节数和耗时，避免输出令牌、完整请求体或带凭据的 URL。Gitee 普通仓库公布的单附件上限为 100 MB，总附件上限为 1 GB，长期发布时也要检查剩余配额。 [Gitee 发行版说明](https://help.gitee.com/repository/release/create) · [Gitee v5 API](https://gitee.com/api/v5/swagger)

实现参考见 [z-note 发布工作流](../.github/workflows/release.yml) 和 [z-note 打包脚本](../scripts/package.ps1)。

## 发一个新版本

先确认工作区与待发布提交一致，检查远端没有同名标签和发行版，再把项目版本、发布说明、工作流推送到源码仓。然后在对应提交上创建并推送标签。以下以尚未使用的 `2.1.9` 为示例，实际执行时替换为本次版本。

~~~powershell
git ls-remote origin refs/tags/v2.1.9
# 确认命令执行成功且远端没有同名标签后再执行。
git tag -a v2.1.9 -m 'z-note 2.1.9'
git push origin refs/tags/v2.1.9
gh run list --repo TrevorZsl/z-note --workflow release.yml --branch v2.1.9 --limit 1
~~~

记下原构建运行编号，并跟到构建、`github-draft` 和 `gitee-preview` 三个任务结束。上传失败时先读失败步骤和平台附件列表。只补传失败的一侧，避免同时运行多个任务写入同一版本的附件。已正式发布的版本使用新标签修复，保留原标签和原附件。

## Gitee 大文件超时后的自动补传

`v2.1.8` 的真实案例中，云端创建了预览版，`SHA256SUMS` 也上传成功，但安装包约 65.5 MB，一次重试在 600 秒时只记录了 11.7 MB 已发送，附件列表仍缺少安装包。这两个数字无法区分持续低速与中途卡住。用一次性 Windows Runner 从本机直连 Gitee 后，补传成功，整个补传任务用时 48 秒，随后从 Gitee 下载回读，两份附件的 SHA-256 均与 GitHub 一致。 [云端超时日志](https://github.com/TrevorZsl/z-note/actions/runs/42600426666) · [本机补传成功日志](https://github.com/TrevorZsl/z-note/actions/runs/42603403228)

遇到同类超时，可以按以下步骤复用这条已验证的链路：

1. 查看原运行的构建任务是否成功，并确认失败的上传任务已结束。核对 Gitee 现有附件，保留通过校验的文件。
2. 选择维护者认可、能连接 GitHub Actions 和直连 Gitee 的 Windows 机器。确认已有 PowerShell 7、Git、GitHub CLI、curl 和可执行的 Python 3.12 或更新版本。将现有 Python 加入启动 Runner 的进程 `PATH`。z-note 的自托管上传步骤复用这个 Python，省去运行中再下载和安装。
3. 在私有源码仓的 **Settings → Actions → Runners → New self-hosted runner** 取官方 Windows Runner，校验下载包。使用独占标签和 `--ephemeral` 注册一次性 Runner。它处理一个任务后自动注销。等待状态显示 `Online` 后再派发补传。若在接单前中止，按 GitHub 的移除流程注销该 Runner。 [GitHub Runner 参考](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
4. 如果 GitHub 需要代理，只给 Runner 进程配置 `https_proxy` / `http_proxy`，将 `gitee.com,foruda.gitee.com` 放入 `no_proxy`。Gitee API 和上传命令也显式绕过 HTTP 代理。如果代理软件接管系统流量（TUN），还要确认软件的分流规则让 Gitee 直连。Runner 通过出站 HTTPS 领任务，无需设置入站端口或开机自启。 [GitHub Runner 代理配置](https://docs.github.com/en/actions/how-tos/manage-runners/use-proxy-servers)
5. 用原构建的运行编号和临时 Runner 标签派发补传，记录新运行编号并等待结果。任务成功后核对 Gitee 的两份附件，再确认一次性 Runner 已从仓库 Runner 列表消失。

在已校验并解压的 Runner 目录打开 PowerShell。以下命令以有源码仓管理权限的 GitHub CLI 登录为前提。注册令牌只在当前进程中使用。

~~~powershell
$sourceRepo = 'TrevorZsl/z-note'
$runnerLabel = 'z-note-gitee-once-219'
$runnerToken = gh api --method POST "repos/$sourceRepo/actions/runners/registration-token" --jq .token
if ($LASTEXITCODE -ne 0 -or -not $runnerToken) { throw '获取 Runner 注册令牌失败' }
try {
    .\config.cmd --unattended --ephemeral --no-default-labels --url "https://github.com/$sourceRepo" --token $runnerToken --name $runnerLabel --labels $runnerLabel --work '_work'
    if ($LASTEXITCODE -ne 0) { throw 'Runner 注册失败' }
} finally {
    Remove-Variable runnerToken
}
.\run.cmd
~~~

Runner 开始监听后，在另一个终端派发任务。原运行编号应取**构建并保存 Artifact 的那一次**，即使该次运行因为 Gitee 上传失败而整体标记为失败，也可以补传。

~~~powershell
$sourceRepo = 'TrevorZsl/z-note'
$sourceRun = '<原构建运行编号>'
$runnerLabel = 'z-note-gitee-once-219'
gh workflow run release.yml --repo $sourceRepo --ref main -f "source_run=$sourceRun" -f "gitee_runner=$runnerLabel"
$retryRun = '<新运行页面地址末尾的数字>'
gh run watch $retryRun --repo $sourceRepo --exit-status
~~~

如果维护者希望以后**每次只推一次标签**就走本机直连路线，可在私有源码仓配置专用 Runner，并把仓库的 Actions 变量 `GITEE_RELEASE_RUNNER` 设为它的标签。该 Runner 在发版时要在线。z-note 工作流已支持这个变量，`v2.1.8` 实际验收的是一次性 Runner 补传，长期在线配置需在目标机器上单独验证。

## 交付前核验

- **同一版本、同一次构建。** 两边的版本标签和附件名一致，各有一个安装包和一个 `SHA256SUMS`。从两个平台读取附件，计算 SHA-256 并与原构建逐一比对。GitHub API 提供的资产摘要也可直接用于比对。
- **验证用户能下载。** 从目标用户的网络用匿名请求读取 Gitee Release API 和实际安装包下载 URL。确认下载完整、字节数正确、SHA-256 一致。对应用内更新，用支持 Gitee 的旧版本验证它能发现更高版本，再验证下载和校验。z-note 从 `2.1.8` 开始支持 Gitee，`2.1.7` 及之前的客户端仅查 GitHub。
- **提交给维护者确认。** 附上 GitHub 草稿和 Gitee 预览版地址、成功任务链接和核验结果。GitHub 点 **Publish release**。Gitee 点 **编辑**，取消 **「这是一个预览版本」** 并保存。正式发布后可再查两边 API 确认状态。

