# img2gif

Windows 桌面版图片转 GIF 小工具，按微信表情包工具的思路重写为 Python 版本。

## 功能

- 拖拽图片文件到窗口
- `Ctrl+V` 粘贴剪贴板图片
- 支持从剪贴板 HTML 中提取图片地址
- 模式 1 / 模式 2 两种量化方式
- 原始 / 40 / 80 / 120 / 200 五种输出尺寸
- 输出文件保存到桌面 `pic2meme-output`
- 自动打开资源管理器并定位输出文件

## 依赖

- Python 3.14+
- Pillow
- PySide6

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

或直接：

```powershell
.\run.ps1
```

## 打包

```powershell
.\.venv\Scripts\python -m pip install pyinstaller
.\.venv\Scripts\pyinstaller -y --clean --noconsole --onefile app.py -n pic2meme-py --icon .\icon.ico
```

## GitHub Actions

- 已包含 Windows 自动打包工作流：`.github/workflows/build-windows.yml`
- 触发方式：
  - push 到 `main`
  - pull request
  - 手动执行 `workflow_dispatch`
- 产物：
  - `pic2meme-py.exe`
  - `pic2meme-py-windows-x64.zip`

## Release 文案

- 首版 release 文案：`docs/release-notes-v0.1.0.md`

## 说明

- 这个版本不依赖“复制文件到剪贴板”，避免原始实现里常见的剪贴板失败问题。
- `dist/`、`build/`、`.venv/`、`release/` 和所有 `.exe` 都已加入忽略，不进版本库。
