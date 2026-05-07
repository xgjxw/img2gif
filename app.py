import os
import re
import subprocess
import sys
import tempfile
import traceback
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageSequence
from PySide6.QtCore import QMimeData, QSize, Qt, QStandardPaths, QUrl
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QKeySequence, QMovie, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


OUTPUT_DIR_NAME = "pic2meme-output"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def desktop_dir() -> Path:
    desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    if desktop:
        return Path(desktop)
    return Path.home()


def output_dir() -> Path:
    target = desktop_dir() / OUTPUT_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def new_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return output_dir() / f"{stamp}.gif"


def new_temp_path(suffix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return Path(tempfile.gettempdir()) / f"img2gif_{stamp}{suffix}"


def reveal_in_file_manager(path: Path) -> None:
    normalized = str(path.resolve())
    try:
        if sys.platform.startswith("win"):
            if path.is_dir():
                subprocess.Popen(["explorer", normalized])
            else:
                subprocess.Popen(["explorer", f"/select,{normalized}"])
            return

        if sys.platform == "darwin":
            if path.is_dir():
                subprocess.Popen(["open", normalized])
            else:
                subprocess.Popen(["open", "-R", normalized])
            return

        target = normalized if path.is_dir() else str(path.parent.resolve())
        subprocess.Popen(["xdg-open", target])
    except Exception:
        pass


def longest_edge_resize(size: tuple[int, int], target: int) -> tuple[int, int]:
    width, height = size
    if target <= 0:
        return width, height
    longest = max(width, height)
    if longest <= 0 or longest == target:
        return width, height
    scale = target / float(longest)
    return max(1, round(width * scale)), max(1, round(height * scale))


def normalize_frame(frame: Image.Image, size_limit: int) -> Image.Image:
    rgba = frame.convert("RGBA")
    target = size_limit
    if target == 0 and max(rgba.size) > 1024:
        target = 1024
    new_size = longest_edge_resize(rgba.size, target)
    if new_size != rgba.size:
        rgba = rgba.resize(new_size, Image.Resampling.LANCZOS)
    return rgba


def quantize_frame(frame: Image.Image, mode: int) -> Image.Image:
    dither = Image.Dither.FLOYDSTEINBERG if mode == 1 else Image.Dither.NONE
    return frame.quantize(
        colors=256,
        method=Image.Quantize.FASTOCTREE,
        dither=dither,
    )


def convert_to_gif(source_path: Path, save_path: Path, mode: int, size_limit: int) -> None:
    with Image.open(source_path) as src:
        frames = []
        durations = []

        for frame in ImageSequence.Iterator(src):
            normalized = normalize_frame(frame, size_limit)
            frames.append(quantize_frame(normalized, mode))
            durations.append(frame.info.get("duration", src.info.get("duration", 100)))

        if not frames:
            normalized = normalize_frame(src, size_limit)
            frames = [quantize_frame(normalized, mode)]
            durations = [src.info.get("duration", 100)]

        save_kwargs = {
            "format": "GIF",
            "save_all": True,
            "append_images": frames[1:],
            "duration": durations,
            "loop": src.info.get("loop", 0),
            "optimize": False,
            "disposal": 2,
        }
        transparency = frames[0].info.get("transparency")
        if transparency is not None:
            save_kwargs["transparency"] = transparency

        frames[0].save(save_path, **save_kwargs)


def file_from_clipboard_image(image: QImage) -> Path:
    tmp = new_temp_path(".png")
    if not image.save(str(tmp), "PNG"):
        raise RuntimeError("无法将剪贴板图片写入临时文件")
    return tmp


def extract_html_image(html: str) -> str | None:
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS and path.exists()


def fetch_remote_image(src: str) -> Path:
    parsed = urllib.parse.urlparse(src)
    if parsed.scheme in ("http", "https"):
        ext = Path(parsed.path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            ext = ".png"
        tmp = new_temp_path(ext or ".png")
        urllib.request.urlretrieve(src, tmp)
        return tmp
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(src)


class CardFrame(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")


class DropFrame(QFrame):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self.window.can_handle_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if self.window.handle_mime(event.mimeData(), remember=True):
            event.acceptProposedAction()
        else:
            event.ignore()


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.current_source: Path | None = None
        self.last_output: Path | None = None
        self.movie: QMovie | None = None

        self.setWindowTitle("img2gif")
        self.resize(820, 680)
        self.setMinimumSize(760, 620)
        self.setAcceptDrops(True)

        icon_path = app_base_dir() / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        header = CardFrame()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 18)
        header_layout.setSpacing(8)

        title = QLabel("img2gif")
        title.setObjectName("title")
        subtitle = QLabel("把静态图、动图或剪贴板图片快速转换成更适合发送的 GIF。")
        subtitle.setObjectName("subtitle")
        self.status_label = QLabel("拖拽图片到窗口，或使用粘贴按钮导入。")
        self.status_label.setObjectName("statusInfo")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addWidget(self.status_label)
        root.addWidget(header)

        option_layout = QGridLayout()
        option_layout.setHorizontalSpacing(16)
        option_layout.setVerticalSpacing(16)

        mode_card = CardFrame()
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(18, 16, 18, 16)
        mode_layout.setSpacing(12)
        mode_layout.addWidget(self.section_title("转换模式"))
        mode_hint = QLabel("模式 1 保留细节更多；模式 2 关闭抖动，边缘更干净。")
        mode_hint.setObjectName("hint")
        mode_layout.addWidget(mode_hint)

        self.mode_group = QButtonGroup(self)
        mode_buttons = QHBoxLayout()
        self.mode_1 = QRadioButton("模式 1 · 细节优先")
        self.mode_2 = QRadioButton("模式 2 · 干净优先")
        self.mode_1.setChecked(True)
        self.mode_group.addButton(self.mode_1, 1)
        self.mode_group.addButton(self.mode_2, 2)
        mode_buttons.addWidget(self.mode_1)
        mode_buttons.addWidget(self.mode_2)
        mode_buttons.addStretch()
        mode_layout.addLayout(mode_buttons)

        size_card = CardFrame()
        size_layout = QVBoxLayout(size_card)
        size_layout.setContentsMargins(18, 16, 18, 16)
        size_layout.setSpacing(12)
        size_layout.addWidget(self.section_title("输出大小"))
        size_hint = QLabel("原始尺寸会自动把超大图片压到最长边 1024，避免结果过重。")
        size_hint.setObjectName("hint")
        size_layout.addWidget(size_hint)

        self.size_group = QButtonGroup(self)
        size_buttons = QHBoxLayout()
        for text, value, checked in [
            ("原始", 0, True),
            ("40", 40, False),
            ("80", 80, False),
            ("120", 120, False),
            ("200", 200, False),
        ]:
            button = QRadioButton(text)
            button.setChecked(checked)
            self.size_group.addButton(button, value)
            size_buttons.addWidget(button)
        size_buttons.addStretch()
        size_layout.addLayout(size_buttons)

        option_layout.addWidget(mode_card, 0, 0)
        option_layout.addWidget(size_card, 0, 1)
        root.addLayout(option_layout)

        content = QHBoxLayout()
        content.setSpacing(16)

        preview_card = CardFrame()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(18, 18, 18, 18)
        preview_layout.setSpacing(12)
        preview_layout.addWidget(self.section_title("预览"))

        self.preview = QLabel("等待导入图片")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(QSize(340, 340))
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setObjectName("preview")
        preview_layout.addWidget(self.preview, stretch=1)

        self.source_label = QLabel("来源：未导入")
        self.source_label.setObjectName("meta")
        self.output_label = QLabel(f"输出目录：{output_dir()}")
        self.output_label.setWordWrap(True)
        self.output_label.setObjectName("meta")
        preview_layout.addWidget(self.source_label)
        preview_layout.addWidget(self.output_label)

        side_panel = QVBoxLayout()
        side_panel.setSpacing(16)

        drop_card = DropFrame(self)
        drop_layout = QVBoxLayout(drop_card)
        drop_layout.setContentsMargins(20, 20, 20, 20)
        drop_layout.setSpacing(10)
        drop_title = QLabel("导入图片")
        drop_title.setObjectName("dropTitle")
        drop_hint = QLabel("支持拖拽文件、粘贴剪贴板图片、或粘贴网页里的图片地址。")
        drop_hint.setWordWrap(True)
        drop_hint.setObjectName("hint")
        shortcut_text = "快捷键：Windows / Linux 用 Ctrl+V，macOS 用 Command+V"
        shortcut_label = QLabel(shortcut_text)
        shortcut_label.setWordWrap(True)
        shortcut_label.setObjectName("hint")
        drop_layout.addWidget(drop_title)
        drop_layout.addWidget(drop_hint)
        drop_layout.addWidget(shortcut_label)
        drop_layout.addStretch()

        action_card = CardFrame()
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(18, 18, 18, 18)
        action_layout.setSpacing(10)
        action_layout.addWidget(self.section_title("操作"))

        self.paste_button = QPushButton("从剪贴板导入")
        self.reconvert_button = QPushButton("按当前参数重新生成")
        self.copy_result_button = QPushButton("复制结果到剪贴板")
        self.open_last_button = QPushButton("定位最后生成文件")
        self.open_dir_button = QPushButton("打开输出目录")

        for button in [
            self.paste_button,
            self.reconvert_button,
            self.copy_result_button,
            self.open_last_button,
            self.open_dir_button,
        ]:
            action_layout.addWidget(button)

        self.clipboard_note = QLabel(
            "复制结果时会同时写入文件路径、文件 URL 和预览图。不同聊天软件对 GIF 剪贴板支持不完全一致。"
        )
        self.clipboard_note.setWordWrap(True)
        self.clipboard_note.setObjectName("hint")
        action_layout.addWidget(self.clipboard_note)
        action_layout.addStretch()

        side_panel.addWidget(drop_card, stretch=1)
        side_panel.addWidget(action_card, stretch=1)

        content.addWidget(preview_card, stretch=5)
        content.addLayout(side_panel, stretch=4)
        root.addLayout(content, stretch=1)

        self.mode_group.idClicked.connect(lambda _id: self.reconvert())
        self.size_group.idClicked.connect(lambda _id: self.reconvert())
        self.paste_button.clicked.connect(self.handle_clipboard)
        self.reconvert_button.clicked.connect(self.reconvert)
        self.copy_result_button.clicked.connect(self.copy_result_to_clipboard)
        self.open_last_button.clicked.connect(self.open_last_output)
        self.open_dir_button.clicked.connect(lambda: reveal_in_file_manager(output_dir()))
        QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self, activated=self.handle_clipboard)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Copy), self, activated=self.copy_result_to_clipboard)

        self.set_buttons_enabled(has_output=False)
        self.apply_styles()

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f4f8f2;
                color: #243428;
                font-size: 14px;
            }
            QFrame#card {
                background: #ffffff;
                border: 1px solid #dfe9dc;
                border-radius: 16px;
            }
            QFrame#dropZone {
                background: #eef8ea;
                border: 2px dashed #9ec28f;
                border-radius: 16px;
            }
            QLabel#title {
                font-size: 30px;
                font-weight: 700;
            }
            QLabel#subtitle {
                font-size: 15px;
                color: #5a6d5e;
            }
            QLabel#statusInfo {
                font-size: 15px;
                color: #2f6d3a;
                background: #edf8ef;
                border-radius: 10px;
                padding: 10px 12px;
            }
            QLabel#sectionTitle {
                font-size: 18px;
                font-weight: 600;
            }
            QLabel#preview {
                background: #f7faf6;
                border: 1px solid #e5eee2;
                border-radius: 14px;
                font-size: 18px;
                color: #7b8d7f;
            }
            QLabel#dropTitle {
                font-size: 22px;
                font-weight: 600;
            }
            QLabel#hint, QLabel#meta {
                color: #627065;
                line-height: 1.5;
            }
            QRadioButton {
                font-size: 15px;
                spacing: 8px;
            }
            QPushButton {
                min-height: 46px;
                border-radius: 12px;
                border: 1px solid #d7e3d4;
                background: #f8fbf7;
                padding: 8px 14px;
                font-size: 15px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #eff6ed;
            }
            QPushButton:pressed {
                background: #e6efe3;
            }
            QPushButton:disabled {
                color: #9aa69d;
                background: #f3f5f2;
            }
            """
        )

    def section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def set_status(self, text: str, tone: str = "info") -> None:
        color_map = {
            "info": ("#edf8ef", "#2f6d3a"),
            "success": ("#edf8ef", "#2f6d3a"),
            "warning": ("#fff7e8", "#8a5b12"),
            "error": ("#fff0f0", "#a53d3d"),
        }
        background, color = color_map.get(tone, color_map["info"])
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"font-size: 15px; color: {color}; background: {background}; border-radius: 10px; padding: 10px 12px;"
        )

    def set_buttons_enabled(self, has_output: bool) -> None:
        self.reconvert_button.setEnabled(self.current_source is not None)
        self.copy_result_button.setEnabled(has_output)
        self.open_last_button.setEnabled(has_output)

    def current_mode(self) -> int:
        return self.mode_group.checkedId() or 1

    def current_size(self) -> int:
        return self.size_group.checkedId()

    def can_handle_mime(self, mime) -> bool:
        if mime.hasUrls() or mime.hasImage():
            return True
        if mime.hasHtml() and extract_html_image(mime.html()):
            return True
        if mime.hasText():
            text = mime.text().strip()
            return text.startswith(("http://", "https://", "file://")) or Path(text).exists()
        return False

    def resolve_source_from_text(self, text: str) -> Path:
        if not text:
            raise RuntimeError("剪贴板内容为空")
        if text.startswith(("http://", "https://", "file://")):
            path = fetch_remote_image(text)
            if not path.exists():
                raise RuntimeError("无法从文本地址读取图片")
            return path

        candidate = Path(text.strip('"'))
        if is_supported_image(candidate):
            return candidate
        raise RuntimeError("剪贴板文本不是可用的图片路径或图片地址")

    def resolve_source(self, mime) -> Path:
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if is_supported_image(path):
                        return path
                else:
                    remote = fetch_remote_image(url.toString())
                    if remote.exists():
                        return remote
            raise RuntimeError("未找到可导入的图片文件")

        if mime.hasImage():
            image = QGuiApplication.clipboard().image()
            if image.isNull():
                image_data = mime.imageData()
                if isinstance(image_data, QImage):
                    image = image_data
                elif hasattr(image_data, "toImage"):
                    image = image_data.toImage()
            if isinstance(image, QImage) and not image.isNull():
                return file_from_clipboard_image(image)
            raise RuntimeError("剪贴板图片为空")

        if mime.hasHtml():
            src = extract_html_image(mime.html())
            if src:
                path = fetch_remote_image(src)
                if path.exists():
                    return path
            raise RuntimeError("HTML 中没有找到可用图片地址")

        if mime.hasText():
            return self.resolve_source_from_text(mime.text().strip())

        raise RuntimeError("暂不支持这种导入方式")

    def handle_mime(self, mime, remember: bool) -> bool:
        try:
            source = self.resolve_source(mime)
            self.convert_source(source, remember=remember)
            return True
        except Exception as exc:
            self.preview.setText("等待导入图片")
            self.preview.setMovie(None)
            self.movie = None
            self.set_status(f"转换失败：{exc}", tone="error")
            self.set_buttons_enabled(has_output=self.last_output is not None and self.last_output.exists())
            return False

    def convert_source(self, source: Path, remember: bool) -> None:
        output = new_output_path()
        convert_to_gif(source, output, self.current_mode(), self.current_size())
        self.last_output = output
        if remember:
            self.current_source = source

        self.source_label.setText(f"来源：{source}")
        self.output_label.setText(f"输出文件：{output}")
        self.show_preview(output)
        self.set_status(f"转换成功，已生成：{output.name}", tone="success")
        self.set_buttons_enabled(has_output=True)
        reveal_in_file_manager(output)

    def show_preview(self, output: Path) -> None:
        self.movie = QMovie(str(output))
        self.preview.setText("")
        self.preview.setMovie(self.movie)
        self.movie.start()

    def reconvert(self) -> None:
        if not self.current_source:
            self.set_status("还没有可重新生成的源图片，请先拖入图片或从剪贴板导入。", tone="warning")
            return
        try:
            self.convert_source(self.current_source, remember=False)
        except Exception as exc:
            self.set_status(f"重新生成失败：{exc}", tone="error")

    def handle_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        self.handle_mime(clipboard.mimeData(), remember=True)

    def copy_result_to_clipboard(self) -> None:
        if not self.last_output or not self.last_output.exists():
            self.set_status("还没有可复制的结果文件。", tone="warning")
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.last_output))])
        mime.setText(str(self.last_output))

        preview_image = QImage(str(self.last_output))
        if not preview_image.isNull():
            mime.setImageData(preview_image)

        QApplication.clipboard().setMimeData(mime)
        self.set_status("已复制结果到剪贴板。部分应用会识别为文件，部分会识别为静态预览图。", tone="success")

    def open_last_output(self) -> None:
        if self.last_output and self.last_output.exists():
            reveal_in_file_manager(self.last_output)
        else:
            reveal_in_file_manager(output_dir())


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("img2gif")
    app.setOrganizationName("img2gif")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
