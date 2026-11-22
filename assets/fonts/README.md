# 内置字体

设置提供 Times New Roman（中文宋体）、微软雅黑、宋体、电脑默认。
前三项离线可用，通过 Win32 `AddFontResourceExW(FR_PRIVATE)` 仅加载到 z-note
进程，不安装系统字体，不修改系统注册表。电脑默认使用 Tk 初始化时的系统默认字体。

来源为 https://github.com/wachin/Microsoft-Fonts 。下载提交与原文件、内置文件
SHA-256 见 `sources.json`。这些文件是公开仓库收集的专有字体，不是开源字体，
公开下载不代表具有随应用重新分发的授权；原版权归各字体权利人所有。

为避免与系统字体冲突，内置家族名称分别为 `ZNote Classic`、`ZNote YaHei`、
`ZNote SimSun`，设置界面仍显示用户熟悉的名称。
Classic 的西文字形来自 Times New Roman，缺少的中文字形来自宋体，
合并为一个字体，让正文、标题画布与标题编辑框都不依赖本机字体回退。
Classic 和宋体在运行时由 Windows 合成粗体，让中英文笔画同步加粗；微软雅黑使用内置粗体。

重建：将 `times.ttf`、`timesbd.ttf`、`msyh.ttc`、`msyhbd.ttc`、`simsun.ttc`
和记录提交号的 `source-commit.txt` 放入 `.runtime-deps/font-sources/`，
安装构建依赖 `fonttools`，运行 `python scripts/prepare_fonts.py`。
应用运行不需要 fonttools。
