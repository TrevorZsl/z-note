# z-note

Windows 桌面便签工具，使用 Python、Tkinter 和系统托盘运行。支持普通文本与待办混排、中文输入、置顶、隐藏、快捷键、已完成记录和回收站。

## 安装与启动

当前验证环境为 Windows、Python 3.12。Python 需要包含 Tkinter，可用以下命令检查安装：

```powershell
python -m tkinter
python -m pip install -r requirements.txt
```

安装依赖后，双击 `启动.bat`。也可以在项目目录运行：

```powershell
python main.py
```

日常更新代码后，从托盘菜单选择“退出”，再双击 `启动.bat`。关闭或隐藏便签窗口不等于退出软件。重复启动时会提示已有实例运行。

`启动.bat` 优先使用 PATH 中的 `pythonw.exe`，找不到时尝试已有的 Codex Python 运行时；这是现有启动方式。设置中的开机启动直接启动 `main.py`。

## 文字与待办转换

在便签正文中按 **Ctrl+Shift+L**，或右键选择“转为文字／转为待办”：

- 没有选区时转换光标所在行；只选中一行的部分文字时，也转换该整行。
- 选中多行时，待办行更多则全部转文字；文字行更多或数量相等则全部转待办。
- 按实际内容行统计，空行也参与，自动折行不重复计数；选区结束在下一行行首时不包含该行。
- 转换后保留选区，可再次触发整批切换；支持一次撤销或重做。新生成的待办默认未完成，原有待办转为待办时保留完成状态。
- 右键点击选区内的行处理整个选区；点击选区外的行，只处理点击行。

设置的“快捷键”区域可修改“文字／待办切换”，自动保存并立即生效；“重置快捷键”恢复默认组合。右键菜单右侧显示当前组合键。此快捷键仅在便签正文内生效，输入法组词时不执行，不占用其他软件的快捷键。

## 字体

设置 → 外观 → 字体可选 **Times New Roman（中文宋体）**、**微软雅黑**、
**宋体**和**电脑默认**。默认使用 Times New Roman（中文宋体），旧版本升级后
同样保持这套搭配。选择后立即应用到所有便签正文与标题，并自动保存。
前三项由软件内置，目标电脑无需安装字体，也无需联网；字体只供当前应用进程使用。
字体来源与构建说明见 `assets/fonts/README.md`。

使用 `powershell -ExecutionPolicy Bypass -File scripts/package.ps1` 构建包含字体的
`dist/z-note.exe`，可直接运行；需要安装包时再编译 `installer/z-note.iss`。
构建环境需要安装 `pyinstaller`、`requirements.txt` 中的依赖。

## 数据与备份

- 数据保存在 `%APPDATA%\z-note\notes.json`，设置页面也显示实际路径。
- 启动读取已有数据时，在同目录的 `backup` 文件夹保留最近 5 份备份。
- 保存时先写临时文件，再替换数据文件。
- 已完成记录和回收站内容也保存在同一个 JSON 文件里。
- 手动备份或迁移时，先从托盘退出软件，再复制整个 `%APPDATA%\z-note` 文件夹。

拆分代码不会迁移数据，不会修改文件格式或已有设置。

## 代码结构

```text
main.py                     应用入口、便签编辑器、窗口、托盘与 Windows 交互
storage.py                  JSON 读写、默认设置与启动备份
启动.bat                    日常启动入口
requirements.txt            运行依赖
assets/icons/               现用图标、源文件及授权说明
docs/产品设计文档.md         早期产品设计参考，当前行为以代码为准
tests/test_storage.py       数据读写与备份测试
tests/test_app_lifecycle.py  单实例、消息分发及退出顺序测试
tests/test_note_editing.py   Windows 界面与编辑交互回归测试
```

数据模块不导入界面代码；`main.py` 直接导入数据函数。便签编辑、中文输入和布局仍保持在一起，便于检查交互关系。

## 验证修改

在项目目录运行：

```powershell
# 无需打开界面的数据测试，使用临时目录
python -m unittest discover -s tests -p test_storage.py -v

# 单实例、消息分发和退出逻辑测试，使用模拟对象
python -m unittest discover -s tests -p test_app_lifecycle.py -v

# 完整回归：包含会创建窗口的 Windows 界面测试
python -m unittest discover -s tests -v
```

界面测试需要可交互的 Windows 桌面，部分断言涉及 DPI、截图和窗口前后顺序。自动化测试与真实中文输入法、实际窗口操作的手工验证需要分别记录。

后续按功能边界逐步整理，每次先记录测试基线，再对照修改后的结果。涉及保存的测试使用临时目录或模拟对象，不使用真实便签数据；涉及布局的修改保留相应交互测试。
