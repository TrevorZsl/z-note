# GitHub 与 Gitee 双平台发布手册

适用于 z-note。一次标签构建生成一份安装包和一份 `SHA256SUMS`，上传到 GitHub 草稿和 Gitee 预览版。下载两边的附件，与原构建逐一比对 SHA-256 后交付链接。正式发布由维护者操作。

> 给执行 Agent：按本文发布指定版本。先查已有标签、Actions 运行和两边的发行版，优先接续已完成的工作。新版本用标签触发构建。在 Actions 构建期间准备本机 Windows Runner。构建和 GitHub 草稿成功后，如果云端 Gitee 任务仍在运行、安装包尚未就绪、且本机 Runner 已在线，切换到原产物补传。两边下载验收完成后再交付。

## 1. 查版本和现有状态

在源码仓根目录用 PowerShell 执行。以下变量供后续命令复用。

~~~powershell
$version = '<目标版本，例如 2.2.8>'
$tag = "v$version"
$sourceRepo = 'TrevorZsl/z-note'
$githubRepo = 'TrevorZsl/z-note-releases'
$giteeRepo = 'trevorzsl/z-note-releases'
$installer = "z-note-setup-$version.exe"
$notes = "releases/$version.md"
$giteeApi = "https://gitee.com/api/v5/repos/$giteeRepo/releases/tags/$tag"
$giteePage = "https://gitee.com/$giteeRepo/releases/tag/$tag"

git status --short
git remote -v
git log -5 --oneline
git tag --list $tag
git ls-remote origin "refs/tags/$tag" "refs/tags/$tag^{}"
gh run list -R $sourceRepo -w release.yml -b $tag -e push -L 10 --json databaseId,headSha,status,conclusion,url
gh release view $tag -R $githubRepo --json tagName,isDraft,url,assets
curl.exe --noproxy gitee.com,foruda.gitee.com -sS --connect-timeout 10 --max-time 30 -w "`nHTTP %{http_code}`n" $giteeApi
~~~

- 确认 `origin` 指向私有源码仓。源码版本在 `version.py` 的 `APP_VERSION`，公开说明在 `releases/<版本>.md`。把版本和说明准备在待发布提交上。确认发布分支和提交，保留工作区里的无关改动。补传用的远端 `main` 也要包含目标版本说明和支持 `source_run` 的工作流。
- 已有远端标签时，核对它指向的提交（附注标签用 `^{}` 那一行）。以同标签、同提交的原始 `push` 运行继续。已有同名发行版时先查状态和附件。任一平台已正式发布且发现产物冲突时，报告冲突，用新版本处理修复。
- 查询失败时区分权限、代理和不存在。GitHub 草稿需要有权访问发布仓的登录。Gitee 匿名接口返回 404 时，再用已授权账号查看发布页或已认证 API，确认是否有预览版。沙箱阻止访问本机代理时，用获准联网的终端执行；保留系统代理设置。
- 纯发版复用已完成的功能回归。现有 Actions 自动运行 `test_updater.py`、`test_storage.py`、`test_app_lifecycle.py` 和 `test_update_ui.py`，保留这道构建检查。无需为了上传再本地重跑全套、重做 UI 回归或先手工打一遍包。新增代码仍按仓库规则验证。安装器或应用内更新本身有改动时，按改动做一次对应的 Windows 冒烟验证；Gitee 仅补传时直接验收附件。

日常发布沿用源码仓 Actions Secrets 中已配置的 `RELEASE_TOKEN` 和 `GITEE_TOKEN`。前者需要 GitHub 发布仓 `Contents: Read and write`，后者需要 Gitee 发布仓写权限。一次性 Runner 的注册权限来自操作者的 GitHub CLI 登录。令牌留在对应 Secrets 或当前进程变量内。

## 2. 只触发一次构建

新版本且确认无同名远端标签时，先推送已确认的发布提交，再在它上面打标签。以下以发布分支为 `main` 为例。已有正确的本地标签时可直接推送该标签。

~~~powershell
$commit = git rev-parse HEAD
git push origin HEAD:refs/heads/main
if ($LASTEXITCODE -ne 0) { throw '发布分支推送失败' }
git tag -a $tag $commit -m "z-note $version"
if ($LASTEXITCODE -ne 0) { throw '创建标签失败' }
git push origin "refs/tags/$tag"
if ($LASTEXITCODE -ne 0) { throw '标签推送失败' }
gh run list -R $sourceRepo -w release.yml -b $tag -c $commit -e push -L 5 --json databaseId,status,conclusion,url
~~~

Actions 列表刚推标签可能暂时为空，等十几秒再查。记下这次标签运行的 `databaseId` 为 `$sourceRun`。查看各任务状态，原运行整体显示失败或取消时也要看任务级结果。

~~~powershell
$sourceRun = '<标签触发的原运行编号>'
gh run view $sourceRun -R $sourceRepo --json status,conclusion,url,jobs --jq '{status,conclusion,url,jobs:[.jobs[]|{name,status,conclusion,databaseId,startedAt,completedAt}]}'
gh api "repos/$sourceRepo/actions/runs/$sourceRun/artifacts" --jq '.artifacts[]|{name,expired,size_in_bytes}'
~~~

`build` 在 GitHub 的 Windows Runner 校验标签和说明、运行测试、调用 `scripts/package.ps1 -Installer`，保存 `z-note-<版本>` Artifact。随后 `github-draft` 和 `gitee-preview` 并行消费它。GitHub 草稿应有两份附件。Gitee 任务在传安装包前先上传或复用 `SHA256SUMS`。

## 3. 构建期间准备 Windows Runner

在能连 GitHub Actions 且能直连 Gitee 的 Windows 机器操作。若仓库变量 `GITEE_RELEASE_RUNNER` 已指向在线专用 Runner，标签运行会直接用它。变量已设置但 Runner 离线时，尽早恢复该 Runner 或准备以下一次性 Runner。变量未设置时标签运行的 Gitee 任务使用 GitHub 云端。日常一次性补传无需修改仓库变量。

优先复用本机已校验的 `.runtime-deps/gitee-actions-runner`。没有副本时，到私有源码仓 **Settings → Actions → Runners → New self-hosted runner**，按页面提供的 Windows 命令下载、校验、解压。目录若仍在接单，先核对当前任务和注册状态，使用空闲副本。

在启动 Runner 的 PowerShell 7 窗口检查工具。需要时把已有 Python 的目录加到当前进程 `PATH`。自托管上传使用本机 Python。

~~~powershell
Get-Command pwsh, git, gh, curl.exe, python -ErrorAction Stop
python -c "import sys; assert sys.version_info >= (3, 12); print(sys.executable)"
gh auth status --hostname github.com
curl.exe --noproxy gitee.com,foruda.gitee.com -fsS --connect-timeout 10 --max-time 20 -o NUL https://gitee.com/api/v5/repos/trevorzsl/z-note-releases
~~~

GitHub 如需代理，在 Runner 启动进程里使用已确认有效的 `https_proxy` / `http_proxy`，并把 `gitee.com,foruda.gitee.com` 加入该进程的 `no_proxy`。开启 TUN 时同时确认分流规则让 Gitee 直连。GitHub 和 Actions Artifact 下载走可用的 GitHub 网络路径，Gitee 走直连，两段分别诊断。

~~~powershell
$sourceRepo = 'TrevorZsl/z-note'
$runnerLabel = "z-note-gitee-once-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$runnerDir = (Resolve-Path '.runtime-deps/gitee-actions-runner').Path
Push-Location $runnerDir
try {
    $runnerToken = gh api --method POST "repos/$sourceRepo/actions/runners/registration-token" --jq .token
    if ($LASTEXITCODE -ne 0 -or -not $runnerToken) { throw '获取 Runner 注册令牌失败' }
    try {
        .\config.cmd --unattended --ephemeral --no-default-labels --url "https://github.com/$sourceRepo" --token $runnerToken --name $runnerLabel --labels $runnerLabel --work '_work'
        if ($LASTEXITCODE -ne 0) { throw 'Runner 注册失败' }
    } finally {
        Remove-Variable runnerToken
    }
    Write-Host "Runner label: $runnerLabel"
    .\run.cmd
} finally {
    Pop-Location
}
~~~

保持该窗口运行，见到 `Listening for Jobs` 后，在另一个终端核对 Runner 在线且空闲。自动化环境可在同一套进程环境中后台启动 `run.cmd`，把标准输出和错误重定向到各自日志；确认监听后继续，避免让长驻进程挡住后续命令。

~~~powershell
gh api "repos/$sourceRepo/actions/runners" --paginate --jq '.runners[] | [.name,.status,.busy,([.labels[].name]|join(","))] | @tsv'
~~~

## 4. 到切换条件就补传

| 看到的状态 | 下一步 |
| --- | --- |
| `build` 和 `github-draft` 成功，Gitee 附件已齐 | 进入下载验收。如果仍启动了闲置的一次性 Runner，注销它。 |
| 两个前置任务成功、原 Artifact 存在、云端 Gitee 仍在跑或排队、确认 Gitee 缺文件、本机已在线 | 取消原运行中仅剩的云端任务，等原运行成为 `completed`，再派发本机补传。若取消期间云端已上传完成，直接验收。 |
| 原运行已结束，Gitee 缺文件 | 跳过取消，派发本机补传。原运行的总体结论可以是 `failure` 或 `cancelled`。 |
| Runner 暂不可用 | 继续跟踪原上传，同时解决 Runner 连通性；先查具体失败步骤。 |
| `build` 失败且没有可用 Artifact | 查看构建失败日志并处理根因；只有无可复用的合格产物时才需要构建。 |
| 已有附件同名但字节数或 SHA-256 不同 | 记录冲突来源、标签和哈希，停止写入冲突附件，核实原产物及发行状态。 |

取消前用任务列表确认只有云端 Gitee 还在运行，避免取消构建或 GitHub 上传。通过 Gitee 发行版附件列表确认安装包状态；无法读取时先处理连通或权限，避免在状态不明时重复写入。可从第 1 步的 Gitee 标签查询取得发行版 `id`，再列附件。匿名接口不可见时，在有权限的 Gitee 发布页检查同样的信息。

~~~powershell
$releaseId = '<Gitee 标签查询返回的发行版 id>'
$attachApi = "https://gitee.com/api/v5/repos/$giteeRepo/releases/$releaseId/attach_files"
$attachmentJson = curl.exe --noproxy gitee.com,foruda.gitee.com -fsS --connect-timeout 10 --max-time 30 $attachApi
if ($LASTEXITCODE -ne 0) { throw '读取 Gitee 附件列表失败' }
$attachmentJson | ConvertFrom-Json | Select-Object name, size
~~~

~~~powershell
# 仅在上述切换条件满足、原运行仍在进行时执行。
gh run cancel $sourceRun -R $sourceRepo
gh run view $sourceRun -R $sourceRepo --json status,conclusion
~~~

原运行状态为 `completed` 后，用上一步打印的**实际标签**派发。原 Artifact 和 GitHub 草稿保持有效。工作流从远端 `main` 取脚本和说明，从 `source_run` 取版本和二进制。

~~~powershell
$runnerLabel = '<Runner 窗口打印的标签>'
gh workflow run release.yml -R $sourceRepo --ref main -f "source_run=$sourceRun" -f "gitee_runner=$runnerLabel"
if ($LASTEXITCODE -ne 0) { throw '派发 Gitee 补传失败' }
~~~

使用派发返回的 URL 取得新运行编号。没有返回 URL 时，查看 Actions 列表中刚派发的 `workflow_dispatch` 运行，并用 Runner 日志核对接单情况。该运行的分支是 `main`，按版本标签筛选会漏掉它。

~~~powershell
gh run list -R $sourceRepo -w release.yml -b main -e workflow_dispatch -L 5 --json databaseId,createdAt,status,conclusion,url
$retryRun = '<补传运行编号>'
gh run watch $retryRun -R $sourceRepo --exit-status
~~~

预期 `build` 和 `github-draft` 为 `skipped`，`gitee-preview` 为 `success`。Gitee 任务会校验原安装包与清单、检查预览版、复用已经匹配的附件、补齐缺失文件。已开始的任务结束后，一次性 Runner 自动注销。若接单前中止，在仓库 Runner 设置按移除流程注销。

### 失败时查当前步骤

- **一直排队**。核对 `gitee_runner` 的标签、Runner 是否在线且空闲、`run.cmd` 是否还在运行、原运行是否已结束。先解决现有任务的阻塞。
- **卡在 `download-artifact`**。检查原运行编号、Artifact 名、过期状态以及本机访问 GitHub 和 Artifact 存储的代理路径。v2.2.6 的这个步骤用时约 10 分 50 秒，安装包实际传到 Gitee 只用约 14 秒。
- **Gitee API 或上传失败**。用 `gh run view $retryRun -R $sourceRepo --log-failed` 查看具体步骤。先核对直连，再看 API 的 HTTP 状态或 curl 的上传字节和耗时。API 请求超时可能发生在创建发行版或枚举附件阶段。curl 超时后先刷新附件列表，服务端可能已收到文件；确认缺失后再用同一 `source_run` 和新的在线一次性 Runner 补传。
- **仅 GitHub 草稿缺附件**。从原运行下载 Artifact 后，用有发布仓写权限的 CLI 针对缺失附件执行 `gh release upload $tag <缺失文件路径> -R $githubRepo`。若已确认草稿不存在，用 `gh release create $tag <安装包路径> <清单路径> -R $githubRepo --draft --title "z-note $version" --notes-file $notes`。部分成功时保留现有正确附件，检查同名冲突，避免使用 `--clobber`。
- **配置类错误**。GitHub 凭据失败核对源码仓 `RELEASE_TOKEN` 与发布仓写权限。Gitee 鉴权失败核对 `GITEE_TOKEN`、目标仓和状态。预览版必须在已初始化的 Gitee 公开发布仓创建。工作流的附件上传使用 `multipart/form-data` 的 `access_token` 和 `file` 字段，已有实现见 [release.yml](../.github/workflows/release.yml)。

## 5. 下载回读与交付

每次验收用新目录，避免旧下载混进本次结果。三个来源各取两份文件，逐项比对两个 SHA-256，再核对清单里的安装包哈希。GitHub 草稿用登录后的 CLI 下载；Gitee 附件以匿名、直连方式从用户实际下载路径取回。三个目录互相独立，连接条件允许时可并行下载。当前发版过程中已经从同一 `source_run` 下载并核验的原 Artifact 可以复用。若临近 Artifact 过期，优先保存原文件和运行编号供验收使用；自动补传仍需要远端 Artifact 可用。

~~~powershell
$verify = Join-Path $PWD ".runtime-deps/release-$version-$(Get-Date -Format yyyyMMdd-HHmmss)"
$artifactDir = Join-Path $verify 'artifact'
$githubDir = Join-Path $verify 'github'
$giteeDir = Join-Path $verify 'gitee'
$null = New-Item -ItemType Directory -Path $artifactDir, $githubDir, $giteeDir

gh run download $sourceRun -R $sourceRepo -n "z-note-$version" -D $artifactDir
if ($LASTEXITCODE -ne 0) { throw '下载原 Artifact 失败' }

$draftJson = gh release view $tag -R $githubRepo --json url,tagName,isDraft,assets
if ($LASTEXITCODE -ne 0) { throw '读取 GitHub 草稿失败' }
$draft = $draftJson | ConvertFrom-Json
$files = @('SHA256SUMS', $installer)
if ($draft.tagName -cne $tag -or $draft.isDraft -ne $true -or
    @($draft.assets).Count -ne 2 -or (Compare-Object $files @($draft.assets.name) -CaseSensitive)) {
    throw 'GitHub 草稿的标签、状态或附件名不符'
}
gh release download $tag -R $githubRepo -p $installer -p SHA256SUMS -D $githubDir
if ($LASTEXITCODE -ne 0) { throw '下载 GitHub 草稿附件失败' }

# 用预览页或已认证 API 确认标签、预览状态和两份附件。
# 匿名标签 API 能返回预览元数据时，也可用于这一步。
$giteeDownload = "https://gitee.com/$giteeRepo/releases/download/$tag"
foreach ($file in $files) {
    curl.exe --noproxy gitee.com,foruda.gitee.com -fLsS --connect-timeout 10 --max-time 300 -o (Join-Path $giteeDir $file) "$giteeDownload/$file"
    if ($LASTEXITCODE -ne 0) { throw "下载 Gitee 附件失败：$file" }
}

$ErrorActionPreference = 'Stop'
foreach ($file in $files) {
    $expected = (Get-FileHash -LiteralPath (Join-Path $artifactDir $file) -Algorithm SHA256).Hash
    foreach ($directory in @($githubDir, $giteeDir)) {
        $actual = (Get-FileHash -LiteralPath (Join-Path $directory $file) -Algorithm SHA256).Hash
        if ($actual -cne $expected) { throw "SHA-256 不一致：$directory / $file" }
    }
    Write-Output "$file  $($expected.ToLowerInvariant())"
}
$installerHash = (Get-FileHash -LiteralPath (Join-Path $artifactDir $installer) -Algorithm SHA256).Hash.ToLowerInvariant()
if ((Get-Content -Raw -LiteralPath (Join-Path $artifactDir 'SHA256SUMS')).TrimEnd() -cne "$installerHash  $installer") {
    throw '原 Artifact 清单与安装包不匹配'
}
$draft.url
$giteePage
~~~

交付时给出版本、原构建运行和补传运行链接、`$draft.url`、`$giteePage` 及两份附件的 SHA-256 核验结果。GitHub 草稿链接从 CLI 实际读取，可能含 `untagged-...`。Gitee 预览版可以被浏览，更新器会过滤预览版，应用内发现新版本留到正式发布之后验证。

维护者确认后，在 GitHub 草稿点击 **Publish release**，选择正式版和 **Latest release**；在 Gitee 预览页点击 **编辑**，取消 **「这是一个预览版本」** 并保存。再核对两个平台的正式状态、版本及匿名下载；若本次改了更新链路，用支持对应平台的旧版本做一次更新验证。z-note 从 2.1.8 开始支持 Gitee。覆盖安装和应用更新专项验收见 [版本发布与更新](版本发布与更新.md)。

## 历史执行记录

| 版本 | 实际结果 |
| --- | --- |
| v2.1.8 | 一次云端重试 600 秒只记录了 11.7 MB 已发送，本机补传任务 48 秒。最后对两边下载文件做了 SHA-256 回读。 [云端重试](https://github.com/TrevorZsl/z-note/actions/runs/42600426666) · [本机补传](https://github.com/TrevorZsl/z-note/actions/runs/42603403228) |
| v2.2.1 | 原运行在云端超时后结束，复用本机 Runner 补传成功。原构建和两平台留存的两个文件各自 SHA-256 一致。 [原运行](https://github.com/TrevorZsl/z-note/actions/runs/42649463586) · [本机补传](https://github.com/TrevorZsl/z-note/actions/runs/42651676794) |
| v2.2.5 | 构建和 GitHub 草稿成功后取消尚未完成的云端 Gitee 任务，原 Artifact 由本机接手。补传任务约 95 秒；留存的原产物及两平台下载文件逐一验过 SHA-256。 [原运行](https://github.com/TrevorZsl/z-note/actions/runs/42842280972) · [本机补传](https://github.com/TrevorZsl/z-note/actions/runs/42843004016) |
| v2.2.6 | 本机补传成功，下载 Artifact 约 10 分 50 秒，Gitee 安装包上传约 14 秒。旧验收目录里有两种同名安装包哈希，其中一份 GitHub 下载副本和清单不匹配。这正是验收时使用全新目录并以原 Artifact 为准的原因。 [原运行](https://github.com/TrevorZsl/z-note/actions/runs/43061724935) · [本机补传](https://github.com/TrevorZsl/z-note/actions/runs/43062112625) |
| v2.2.7 | 查阅记录时，原构建成功，GitHub 草稿有两份附件，云端 Gitee 步骤报 30 秒 HTTP 请求超时。后续接手时先查两平台现状，原构建编号为 `43258186176`。 [原运行](https://github.com/TrevorZsl/z-note/actions/runs/43258186176) |
| v2.2.8 | 构建与 GitHub 草稿一次成功。第一次本机补传在 checkout 失败：全局 git 配置的代理指向已停止的本机代理；改为给 Runner 进程注入 `GIT_CONFIG_COUNT` 覆盖 `http.proxy`/`https.proxy`（不动全局配置）后重新派发。Artifact 下载约 26 分钟，任务被 15 分钟超时取消时 Gitee 上传已完成，匿名回读两份附件哈希一致。验收下载的教训：草稿安装包的浏览器 URL 匿名返回 404，须用登录后的 API 附件端点（`Accept: application/octet-stream`）下载；gh 下载先写临时文件再解压，目录为空不代表未开始。 [原运行](https://github.com/TrevorZsl/z-note/actions/runs/43566619819) · [本机补传](https://github.com/TrevorZsl/z-note/actions/runs/43567272849) |
| v2.2.9 | 构建与 GitHub 草稿一次成功；确认 Gitee 预览版零附件后取消云端 Gitee 任务，本机 Runner 补传约 1 分钟完成。本次代理 7890 可用而 GitHub 直连被重置，与 v2.2.8 相反：Runner 进程保持全局 git 代理、只设 `no_proxy=gitee.com,foruda.gitee.com`，未注入空代理覆盖，Artifact 下载仅数十秒。三来源（原 Artifact、GitHub 草稿、Gitee）回读两份附件 SHA-256 全部一致。 [原运行](https://github.com/TrevorZsl/z-note/actions/runs/43622279663) · [本机补传](https://github.com/TrevorZsl/z-note/actions/runs/43622639846) |

跨项目复用时，替换源码仓、两个发布仓、版本来源、附件名称和构建命令即可沿用这条链路。发布仓应先初始化默认分支，公开发行说明与附件放在发布仓，工作流和构建产物留在源码仓。
