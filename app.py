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
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QMovie, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


OUTPUT_DIR_NAME = "pic2meme-output"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def output_dir() -> Path:
    desktop = Path.home() / "Desktop"
    target = desktop / OUTPUT_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def new_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return output_dir() / f"{stamp}.gif"


def new_temp_path(suffix: str) -> Path:
    return Path(tempfile.gettempdir()) / f"pic2meme_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{suffix}"


def open_in_explorer(path: Path) -> None:
    try:
        normalized = os.path.normpath(str(path))
        if path.is_dir():
            subprocess.Popen(["explorer", normalized])
        else:
            subprocess.Popen(["explorer", f"/select,{normalized}"])
    except Exception:
        subprocess.Popen(["explorer", os.path.normpath(str(output_dir()))])


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

        loop = src.info.get("loop", 0)
        transparency = frames[0].info.get("transparency")
        save_kwargs = {
            "save_all": True,
            "append_images": frames[1:],
            "duration": durations,
            "loop": loop,
            "optimize": False,
            "disposal": 2,
        }
        if transparency is not None:
            save_kwargs["transparency"] = transparency

        frames[0].save(save_path, format="GIF", **save_kwargs)


def file_from_clipboard_image(image: QImage) -> Path:
    tmp = new_temp_path(".png")
    if not image.save(str(tmp), "PNG"):
        raise RuntimeError("无法把剪贴板图片写入临时文件")
    return tmp


def extract_html_image(html: str) -> str | None:
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def fetch_html_image(src: str) -> Path:
    parsed = urllib.parse.urlparse(src)
    if parsed.scheme in ("http", "https"):
        ext = Path(parsed.path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            ext = ".gif"
        tmp = new_temp_path(ext or ".gif")
        urllib.request.urlretrieve(src, tmp)
        return tmp
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(src)


class DropFrame(QFrame):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #f6fff3; border: 2px dashed #a4c79a; border-radius: 10px; }"
        )

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

        self.setWindowTitle("微信表情包工具 Py")
        self.resize(560, 620)
        self.setMinimumSize(560, 620)
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        self.notice = QLabel("拖拽图片到窗口，或按 Ctrl+V 粘贴图片")
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setStyleSheet("font-size: 22px; padding: 14px; background: white; border-radius: 8px;")
        root.addWidget(self.notice)

        self.mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.make_tag("转换模式："))
        self.mode_1 = QRadioButton("模式1")
        self.mode_2 = QRadioButton("模式2")
        self.mode_1.setChecked(True)
        self.mode_group.addButton(self.mode_1, 1)
        self.mode_group.addButton(self.mode_2, 2)
        mode_row.addWidget(self.mode_1)
        mode_row.addSpacing(24)
        mode_row.addWidget(self.mode_2)
        mode_row.addStretch()
        root.addLayout(mode_row)

        self.size_group = QButtonGroup(self)
        size_row = QHBoxLayout()
        size_row.addWidget(self.make_tag("输出大小："))
        for text, value, checked in [
            ("原始", 0, True),
            ("40", 40, False),
            ("80", 80, False),
            ("120", 120, False),
            ("200", 200, False),
        ]:
            btn = QRadioButton(text)
            btn.setChecked(checked)
            self.size_group.addButton(btn, value)
            size_row.addWidget(btn)
        size_row.addStretch()
        root.addLayout(size_row)

        self.drop_frame = DropFrame(self)
        frame_layout = QVBoxLayout(self.drop_frame)
        frame_layout.setContentsMargins(16, 16, 16, 16)
        frame_layout.setSpacing(12)

        self.preview = QLabel("等待图片")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(260, 260)
        self.preview.setStyleSheet("background: white; border-radius: 8px; font-size: 18px;")
        frame_layout.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignCenter)

        self.path_label = QLabel(f"输出目录：{output_dir()}")
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("font-size: 14px; color: #555;")
        frame_layout.addWidget(self.path_label)

        root.addWidget(self.drop_frame, stretch=1)

        bottom = QHBoxLayout()
        self.open_dir_btn = QPushButton("打开输出目录")
        self.open_last_btn = QPushButton("定位最后文件")
        self.paste_btn = QPushButton("粘贴转换")
        bottom.addWidget(self.open_dir_btn)
        bottom.addWidget(self.open_last_btn)
        bottom.addWidget(self.paste_btn)
        root.addLayout(bottom)

        self.mode_group.idClicked.connect(lambda _id: self.reconvert())
        self.size_group.idClicked.connect(lambda _id: self.reconvert())
        self.open_dir_btn.clicked.connect(lambda: open_in_explorer(output_dir()))
        self.open_last_btn.clicked.connect(self.open_last_output)
        self.paste_btn.clicked.connect(self.handle_clipboard)
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self.handle_clipboard)

        self.setStyleSheet(
            """
            QWidget { background: #dff2d9; }
            QRadioButton { font-size: 18px; }
            QPushButton {
                font-size: 18px;
                min-height: 44px;
                background: white;
                border: none;
                border-radius: 8px;
                padding: 6px 16px;
            }
            QPushButton:hover { background: #f3f3f3; }
            """
        )

    def make_tag(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 20px;")
        return label

    def current_mode(self) -> int:
        return self.mode_group.checkedId() or 1

    def current_size(self) -> int:
        return self.size_group.checkedId()

    def can_handle_mime(self, mime) -> bool:
        if mime.hasUrls() or mime.hasImage():
            return True
        if mime.hasHtml():
            return extract_html_image(mime.html()) is not None
        return False

    def resolve_source(self, mime) -> Path:
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.exists():
                        return path
            raise RuntimeError("未找到支持的本地图片文件")

        if mime.hasImage():
            image = QGuiApplication.clipboard().image()
            if image.isNull():
                image = mime.imageData()
            if isinstance(image, QImage) and not image.isNull():
                return file_from_clipboard_image(image)
            raise RuntimeError("剪贴板图片为空")

        if mime.hasHtml():
            src = extract_html_image(mime.html())
            if not src:
                raise RuntimeError("HTML 中没有找到图片地址")
            path = fetch_html_image(src)
            if not path.exists():
                raise RuntimeError("图片下载失败")
            return path

        raise RuntimeError("不支持的输入类型")

    def handle_mime(self, mime, remember: bool) -> bool:
        try:
            source = self.resolve_source(mime)
            self.convert_source(source, remember=remember)
            return True
        except Exception as exc:
            self.notice.setText(f"转换失败：{exc}")
            self.preview.setText("等待图片")
            self.preview.setMovie(None)
            self.movie = None
            return False

    def convert_source(self, source: Path, remember: bool) -> None:
        output = new_output_path()
        convert_to_gif(source, output, self.current_mode(), self.current_size())
        self.last_output = output
        if remember:
            self.current_source = source
        self.notice.setText(f"转换成功：已保存到 {output}")
        self.show_preview(output)
        open_in_explorer(output)

    def show_preview(self, output: Path) -> None:
        self.movie = QMovie(str(output))
        self.preview.setText("")
        self.preview.setMovie(self.movie)
        self.movie.start()

    def reconvert(self) -> None:
        if not self.current_source:
            return
        try:
            self.convert_source(self.current_source, remember=False)
        except Exception as exc:
            self.notice.setText(f"转换失败：{exc}")

    def handle_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        self.handle_mime(clipboard.mimeData(), remember=True)

    def open_last_output(self) -> None:
        if self.last_output and self.last_output.exists():
            open_in_explorer(self.last_output)
        else:
            open_in_explorer(output_dir())


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
