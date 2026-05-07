import subprocess
import sys
import tempfile
import traceback
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageSequence
from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, QStandardPaths, QUrl
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QKeySequence, QMovie, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


OUTPUT_DIR_NAME = "pic2meme-output"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
SIZE_PRESETS = [
    ("RAW", 0),
    ("S", 40),
    ("M", 80),
    ("L", 120),
    ("XL", 200),
]


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


def new_temp_path(suffix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return Path(tempfile.gettempdir()) / f"img2gif_{stamp}{suffix}"


def new_output_path(ext: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return output_dir() / f"{stamp}{ext}"


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


def convert_to_png(source_path: Path, save_path: Path, size_limit: int) -> None:
    with Image.open(source_path) as src:
        frame = next(ImageSequence.Iterator(src), src)
        normalized = normalize_frame(frame, size_limit)
        normalized.save(save_path, format="PNG")


def convert_output(source_path: Path, save_path: Path, output_format: str, mode: int, size_limit: int) -> None:
    if output_format == "gif":
        convert_to_gif(source_path, save_path, mode, size_limit)
        return
    if output_format == "png":
        convert_to_png(source_path, save_path, size_limit)
        return
    raise RuntimeError(f"Unsupported format: {output_format}")


def file_from_clipboard_image(image: QImage) -> Path:
    tmp = new_temp_path(".png")
    if not image.save(str(tmp), "PNG"):
        raise RuntimeError("Cannot write clipboard image to a temp file")
    return tmp


def extract_html_image(html: str) -> str | None:
    import re

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


class SidebarButton(QPushButton):
    def __init__(self, text: str, active: bool = False) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setChecked(active)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("sidebarActive" if active else "sidebarButton")


class ChipButton(QPushButton):
    def __init__(self, text: str, checked: bool = False) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("chipButton")


class UtilityButton(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("utilityButton")


class CardFrame(QFrame):
    def __init__(self, object_name: str = "card") -> None:
        super().__init__()
        self.setObjectName(object_name)


class CloudDropZone(QFrame):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setMinimumHeight(340)

        self.clouds = []
        for _ in range(4):
            bubble = QLabel(self)
            bubble.setObjectName("cloudBubble")
            bubble.lower()
            self.clouds.append(bubble)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(24, 24, 24, 24)
        self.content_layout.setSpacing(14)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_circle = QLabel("UP")
        self.icon_circle.setObjectName("dropIcon")
        self.icon_circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_circle.setFixedSize(92, 92)

        self.title_label = QLabel("Drop Your Images Here!")
        self.title_label.setObjectName("dropTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.subtitle_label = QLabel("OR CLICK TO BROWSE FILES")
        self.subtitle_label.setObjectName("dropSubtitle")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.preview_label = QLabel("")
        self.preview_label.setObjectName("mediaPreview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(260, 180)
        self.preview_label.hide()

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("dropMeta")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.movie: QMovie | None = None

        self.content_layout.addWidget(self.icon_circle, alignment=Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(self.title_label)
        self.content_layout.addWidget(self.subtitle_label)
        self.content_layout.addWidget(self.preview_label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(self.meta_label)

        self.reset_prompt()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.content.setGeometry(self.rect())
        positions = [
            QRect(-10, 10, 108, 108),
            QRect(self.width() - 95, 36, 76, 76),
            QRect(self.width() - 160, self.height() - 110, 132, 132),
            QRect(28, self.height() - 72, 66, 66),
        ]
        for bubble, geometry in zip(self.clouds, positions):
            bubble.setGeometry(geometry)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.window.import_from_dialog()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self.window.can_handle_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if self.window.handle_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def reset_prompt(self) -> None:
        self.clear_media()
        self.title_label.setText("Drop Your Images Here!")
        self.subtitle_label.setText("OR CLICK TO BROWSE FILES")
        self.meta_label.setText("")
        self.icon_circle.show()
        self.title_label.show()
        self.subtitle_label.show()
        self.preview_label.hide()

    def clear_media(self) -> None:
        self.preview_label.setMovie(None)
        self.preview_label.clear()
        self.movie = None

    def show_media(self, path: Path, headline: str, detail: str) -> None:
        self.icon_circle.hide()
        self.title_label.setText(headline)
        self.subtitle_label.setText(detail)
        self.preview_label.show()
        self.meta_label.setText(path.name)

        suffix = path.suffix.lower()
        self.clear_media()
        if suffix == ".gif":
            self.movie = QMovie(str(path))
            if self.movie.isValid():
                self.preview_label.setMovie(self.movie)
                self.movie.start()
                return

        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                QSize(420, 240),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            return

        self.preview_label.setText(path.name)


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.current_source: Path | None = None
        self.last_output: Path | None = None
        self.recent_outputs: list[Path] = []
        self.setWindowTitle("MemeEngine - Kawaii Creator")
        self.resize(1180, 820)
        self.setMinimumSize(1080, 760)

        icon_path = app_base_dir() / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self.build_sidebar())
        root.addWidget(self.build_main_area(), stretch=1)

        self.fab = QPushButton("+", self)
        self.fab.setObjectName("fabButton")
        self.fab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fab.setFixedSize(60, 60)
        self.fab.clicked.connect(lambda: reveal_in_file_manager(output_dir()))

        QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self, activated=self.import_from_clipboard)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Copy), self, activated=self.copy_result_to_clipboard)

        self.set_output_actions_enabled(False)
        self.apply_styles()
        self.show_toast("Drop, paste, or browse an image to get started.", "info")

    def build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(18)

        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(2)
        title = QLabel("MemeEngine")
        title.setObjectName("brandTitle")
        subtitle = QLabel("KAWAII CREATOR")
        subtitle.setObjectName("brandSubtitle")
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand)

        self.maker_button = SidebarButton("MAKER", active=True)
        self.collection_button = SidebarButton("COLLECTION")
        self.settings_button = SidebarButton("SETTINGS")

        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        for button in [self.maker_button, self.collection_button, self.settings_button]:
            nav_group.addButton(button)
            layout.addWidget(button)

        self.collection_button.clicked.connect(lambda: reveal_in_file_manager(output_dir()))
        self.settings_button.clicked.connect(self.clear_current)

        layout.addStretch()

        profile = CardFrame("profileCard")
        profile_layout = QHBoxLayout(profile)
        profile_layout.setContentsMargins(14, 14, 14, 14)
        profile_layout.setSpacing(12)

        avatar = QLabel("ME")
        avatar.setObjectName("avatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(44, 44)

        profile_text = QVBoxLayout()
        profile_text.setContentsMargins(0, 0, 0, 0)
        profile_text.setSpacing(1)
        name = QLabel("Kawaii User")
        name.setObjectName("profileName")
        plan = QLabel("PRO MEMBER")
        plan.setObjectName("profilePlan")
        profile_text.addWidget(name)
        profile_text.addWidget(plan)

        profile_layout.addWidget(avatar)
        profile_layout.addLayout(profile_text)
        layout.addWidget(profile)
        return sidebar

    def build_main_area(self) -> QWidget:
        main = QWidget()
        main.setObjectName("mainArea")
        outer = QVBoxLayout(main)
        outer.setContentsMargins(34, 24, 34, 24)
        outer.setSpacing(24)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.toast = QLabel("")
        self.toast.setObjectName("toastInfo")
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.toast, alignment=Qt.AlignmentFlag.AlignHCenter)

        hero = QWidget()
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 0)
        hero_layout.setSpacing(4)
        hero_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Make Some Magic!")
        title.setObjectName("heroTitle")
        subtitle = QLabel("Drop, squish, and share your cutest ideas")
        subtitle.setObjectName("heroSubtitle")
        hero_layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(hero)

        self.drop_zone = CloudDropZone(self)
        outer.addWidget(self.drop_zone)

        self.start_button = QPushButton("Start Magic ✨")
        self.start_button.setObjectName("startButton")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.clicked.connect(self.start_magic)
        outer.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        cards = QHBoxLayout()
        cards.setSpacing(18)
        cards.addWidget(self.build_format_card(), stretch=3)
        cards.addWidget(self.build_size_card(), stretch=2)
        outer.addLayout(cards)

        outer.addWidget(self.build_action_panel())
        return main

    def build_format_card(self) -> QWidget:
        card = CardFrame("pinkCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Format")
        title.setObjectName("cardTitle")
        icon = QLabel("✦")
        icon.setObjectName("cardIcon")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(icon)
        layout.addLayout(header)

        self.format_group = QButtonGroup(self)
        self.format_group.setExclusive(True)
        format_row = QHBoxLayout()
        for text, key, checked in [("GIF", "gif", True), ("PNG", "png", False)]:
            button = ChipButton(text, checked=checked)
            self.format_group.addButton(button)
            button.setProperty("value", key)
            format_row.addWidget(button)
        layout.addLayout(format_row)

        mode_title = QLabel("Style")
        mode_title.setObjectName("miniLabel")
        layout.addWidget(mode_title)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        mode_row = QHBoxLayout()
        for text, value, checked in [("Mode 1", 1, True), ("Mode 2", 2, False)]:
            button = ChipButton(text, checked=checked)
            self.mode_group.addButton(button)
            button.setProperty("value", value)
            mode_row.addWidget(button)
        layout.addLayout(mode_row)
        return card

    def build_size_card(self) -> QWidget:
        card = CardFrame("yellowCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Size")
        title.setObjectName("cardTitle")
        icon = QLabel("▣")
        icon.setObjectName("cardIcon")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(icon)
        layout.addLayout(header)

        self.size_group = QButtonGroup(self)
        self.size_group.setExclusive(True)
        size_row = QHBoxLayout()
        for text, value in SIZE_PRESETS:
            button = ChipButton(text, checked=(value == 80))
            button.setProperty("value", value)
            self.size_group.addButton(button)
            size_row.addWidget(button)
        layout.addLayout(size_row)
        return card

    def build_action_panel(self) -> QWidget:
        card = CardFrame("actionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        title = QLabel("Toolbox")
        title.setObjectName("toolboxTitle")
        layout.addWidget(title)

        actions = QGridLayout()
        actions.setHorizontalSpacing(12)
        actions.setVerticalSpacing(12)

        self.import_button = UtilityButton("Paste / Import")
        self.copy_button = UtilityButton("Copy Result")
        self.open_current_button = UtilityButton("Open Current")
        self.open_folder_button = UtilityButton("Open Folder")
        self.clear_button = UtilityButton("Clear")

        self.import_button.clicked.connect(self.import_from_clipboard)
        self.copy_button.clicked.connect(self.copy_result_to_clipboard)
        self.open_current_button.clicked.connect(self.open_current_output)
        self.open_folder_button.clicked.connect(lambda: reveal_in_file_manager(output_dir()))
        self.clear_button.clicked.connect(self.clear_current)

        buttons = [
            self.import_button,
            self.copy_button,
            self.open_current_button,
            self.open_folder_button,
            self.clear_button,
        ]
        positions = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]
        for button, (row, col) in zip(buttons, positions):
            actions.addWidget(button, row, col)

        layout.addLayout(actions)

        recent_title = QLabel("Recent Results")
        recent_title.setObjectName("recentTitle")
        layout.addWidget(recent_title)

        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentList")
        self.recent_list.itemDoubleClicked.connect(self.open_recent_item)
        layout.addWidget(self.recent_list)
        return card

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        margin = 24
        self.fab.move(self.width() - self.fab.width() - margin, self.height() - self.fab.height() - margin)

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f8f9fa;
                color: #4e4550;
                font-family: "Plus Jakarta Sans", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QWidget#sidebar {
                background: #f2f4f5;
            }
            QWidget#mainArea {
                background: #f8f9fa;
            }
            QFrame#card, QFrame#pinkCard, QFrame#yellowCard, QFrame#actionCard, QFrame#profileCard {
                background: #ffffff;
                border: 1px solid rgba(128, 81, 94, 0.06);
                border-radius: 26px;
            }
            QFrame#pinkCard {
                background: #ffd9e1;
            }
            QFrame#yellowCard {
                background: #f4e48a;
            }
            QFrame#actionCard {
                background: #ffffff;
            }
            QFrame#dropZone {
                background: #ffffff;
                border-radius: 42px;
                border: 1px solid rgba(128, 81, 94, 0.04);
            }
            QLabel#cloudBubble {
                background: rgba(255, 255, 255, 0.96);
                border-radius: 54px;
            }
            QLabel#brandTitle {
                font-size: 30px;
                font-weight: 800;
                color: #80515e;
            }
            QLabel#brandSubtitle {
                font-size: 12px;
                font-weight: 800;
                color: #9b8f92;
                letter-spacing: 1.8px;
            }
            QPushButton#sidebarButton, QPushButton#sidebarActive {
                min-height: 48px;
                text-align: left;
                border: none;
                border-radius: 24px;
                padding: 0 18px;
                font-size: 14px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton#sidebarButton {
                background: transparent;
                color: #72686b;
            }
            QPushButton#sidebarButton:hover {
                background: #eceff1;
            }
            QPushButton#sidebarActive {
                background: #ffc2d1;
                color: #7b4d5a;
            }
            QLabel#avatar {
                background: #f2b7c5;
                color: #ffffff;
                border-radius: 22px;
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#profileName {
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#profilePlan {
                font-size: 10px;
                font-weight: 800;
                color: #918689;
                letter-spacing: 1px;
            }
            QLabel#toastInfo {
                min-width: 360px;
                max-width: 520px;
                border-radius: 28px;
                padding: 14px 26px;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#heroTitle {
                font-size: 40px;
                font-weight: 800;
                color: #80515e;
            }
            QLabel#heroSubtitle {
                font-size: 18px;
                font-style: italic;
                color: #9a8d92;
            }
            QLabel#dropIcon {
                background: #ffc2d1;
                color: #80515e;
                border-radius: 46px;
                font-size: 24px;
                font-weight: 800;
            }
            QLabel#dropTitle {
                font-size: 26px;
                font-weight: 800;
                color: #5a5255;
            }
            QLabel#dropSubtitle {
                font-size: 13px;
                font-weight: 800;
                color: #a79da1;
                letter-spacing: 1.6px;
            }
            QLabel#dropMeta {
                font-size: 13px;
                color: #7b7175;
            }
            QLabel#mediaPreview {
                background: rgba(248, 249, 250, 0.9);
                border-radius: 20px;
                padding: 10px;
            }
            QPushButton#startButton {
                min-width: 230px;
                min-height: 70px;
                border: none;
                border-radius: 34px;
                background: #aef2c2;
                color: #2a6a45;
                font-size: 28px;
                font-weight: 800;
                padding: 0 30px;
            }
            QPushButton#startButton:hover {
                background: #9ae9b2;
            }
            QPushButton#startButton:pressed {
                background: #8edca5;
            }
            QLabel#cardTitle {
                font-size: 22px;
                font-weight: 800;
                color: #4e4550;
            }
            QLabel#cardIcon {
                font-size: 22px;
                font-weight: 800;
                color: #6a5d61;
            }
            QLabel#miniLabel {
                font-size: 12px;
                font-weight: 800;
                color: #6f6166;
                letter-spacing: 1px;
            }
            QPushButton#chipButton {
                min-height: 48px;
                border-radius: 22px;
                border: 2px solid rgba(128, 81, 94, 0.08);
                background: rgba(255, 255, 255, 0.45);
                color: #5a5054;
                font-size: 16px;
                font-weight: 800;
                padding: 0 22px;
            }
            QPushButton#chipButton:checked {
                background: #ffffff;
                border-color: rgba(128, 81, 94, 0.18);
            }
            QLabel#toolboxTitle, QLabel#recentTitle {
                font-size: 18px;
                font-weight: 800;
                color: #5a5054;
            }
            QPushButton#utilityButton {
                min-height: 44px;
                border-radius: 18px;
                border: 1px solid rgba(128, 81, 94, 0.08);
                background: #ffffff;
                color: #5a5054;
                font-size: 14px;
                font-weight: 700;
                padding: 0 16px;
            }
            QPushButton#utilityButton:hover {
                background: #f6f7f8;
            }
            QPushButton#utilityButton:disabled {
                color: #b2a9ac;
                background: #fafafa;
            }
            QListWidget#recentList {
                min-height: 120px;
                background: #fbfbfc;
                border: 1px solid rgba(128, 81, 94, 0.08);
                border-radius: 20px;
                padding: 8px;
            }
            QListWidget#recentList::item {
                padding: 10px 12px;
                border-radius: 12px;
            }
            QListWidget#recentList::item:selected {
                background: #f4e48a;
                color: #514700;
            }
            QPushButton#fabButton {
                border: none;
                border-radius: 30px;
                background: #80515e;
                color: #ffffff;
                font-size: 28px;
                font-weight: 700;
            }
            QPushButton#fabButton:hover {
                background: #8a5a67;
            }
            """
        )

    def show_toast(self, message: str, tone: str) -> None:
        colors = {
            "info": ("#edf8ef", "#2f6d3a"),
            "success": ("#aef2c2", "#2a6a45"),
            "warning": ("#fff0be", "#6a5f12"),
            "error": ("#ffdad6", "#93000a"),
        }
        background, foreground = colors.get(tone, colors["info"])
        self.toast.setText(message)
        self.toast.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 28px; padding: 14px 26px; font-size: 18px; font-weight: 700;"
        )

    def current_format(self) -> str:
        button = self.format_group.checkedButton()
        return button.property("value") if button else "gif"

    def current_mode(self) -> int:
        button = self.mode_group.checkedButton()
        return int(button.property("value")) if button else 1

    def current_size(self) -> int:
        button = self.size_group.checkedButton()
        return int(button.property("value")) if button else 80

    def can_handle_mime(self, mime) -> bool:
        if mime.hasUrls() or mime.hasImage():
            return True
        if mime.hasHtml() and extract_html_image(mime.html()):
            return True
        if mime.hasText():
            text = mime.text().strip()
            return text.startswith(("http://", "https://", "file://")) or Path(text.strip('"')).exists()
        return False

    def import_from_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )
        if path:
            self.set_source(Path(path))

    def import_from_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        self.handle_mime(clipboard.mimeData())

    def resolve_source_from_text(self, text: str) -> Path:
        if not text:
            raise RuntimeError("Clipboard text is empty")
        if text.startswith(("http://", "https://", "file://")):
            path = fetch_remote_image(text)
            if not path.exists():
                raise RuntimeError("Cannot load image from clipboard text")
            return path

        candidate = Path(text.strip('"'))
        if is_supported_image(candidate):
            return candidate
        raise RuntimeError("Clipboard text is not an image path or image URL")

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
            raise RuntimeError("No supported image found in dropped items")

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
            raise RuntimeError("Clipboard image is empty")

        if mime.hasHtml():
            src = extract_html_image(mime.html())
            if src:
                path = fetch_remote_image(src)
                if path.exists():
                    return path
            raise RuntimeError("No usable image found in clipboard HTML")

        if mime.hasText():
            return self.resolve_source_from_text(mime.text().strip())

        raise RuntimeError("This import type is not supported")

    def handle_mime(self, mime) -> bool:
        try:
            source = self.resolve_source(mime)
            self.set_source(source)
            return True
        except Exception as exc:
            self.show_toast(f"Import failed: {exc}", "error")
            return False

    def set_source(self, source: Path) -> None:
        self.current_source = source
        self.last_output = None
        self.drop_zone.show_media(source, "Image Loaded!", "Tap Start Magic when your settings look right")
        self.show_toast("Success! Magic Ready.", "success")
        self.set_output_actions_enabled(False)

    def set_output_actions_enabled(self, enabled: bool) -> None:
        self.copy_button.setEnabled(enabled)
        self.open_current_button.setEnabled(enabled)

    def start_magic(self) -> None:
        if not self.current_source:
            self.show_toast("Import an image first, then hit Start Magic.", "warning")
            return

        output_format = self.current_format()
        ext = ".gif" if output_format == "gif" else ".png"
        output = new_output_path(ext)

        try:
            convert_output(
                self.current_source,
                output,
                output_format,
                self.current_mode(),
                self.current_size(),
            )
        except Exception as exc:
            self.show_toast(f"Magic failed: {exc}", "error")
            return

        self.last_output = output
        self.drop_zone.show_media(output, "Success! Magic Ready.", output.name)
        self.update_recent_outputs(output)
        self.set_output_actions_enabled(True)
        self.show_toast(f"Success! {output.name} is ready.", "success")
        reveal_in_file_manager(output)

    def copy_result_to_clipboard(self) -> None:
        if not self.last_output or not self.last_output.exists():
            self.show_toast("No result yet. Run Start Magic first.", "warning")
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.last_output))])
        mime.setText(str(self.last_output))
        preview_image = QImage(str(self.last_output))
        if not preview_image.isNull():
            mime.setImageData(preview_image)
        QApplication.clipboard().setMimeData(mime)
        self.show_toast("Result copied. Some apps will treat it as a file, some as a preview image.", "info")

    def open_current_output(self) -> None:
        if not self.last_output or not self.last_output.exists():
            self.show_toast("No current result to open.", "warning")
            return
        reveal_in_file_manager(self.last_output)

    def clear_current(self) -> None:
        self.current_source = None
        self.last_output = None
        self.drop_zone.reset_prompt()
        self.set_output_actions_enabled(False)
        self.show_toast("Cleared. Drop or paste a new image anytime.", "info")

    def update_recent_outputs(self, output: Path) -> None:
        self.recent_outputs = [item for item in self.recent_outputs if item.exists() and item != output]
        self.recent_outputs.insert(0, output)
        self.recent_outputs = self.recent_outputs[:8]

        self.recent_list.clear()
        for path in self.recent_outputs:
            item = QListWidgetItem(f"{path.name}   |   {path.parent.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.recent_list.addItem(item)

    def open_recent_item(self, item: QListWidgetItem) -> None:
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not raw:
            return
        path = Path(raw)
        if not path.exists():
            self.show_toast("That recent result is no longer available.", "warning")
            return
        self.last_output = path
        self.drop_zone.show_media(path, "Previewing Recent Result", path.name)
        self.set_output_actions_enabled(True)
        reveal_in_file_manager(path)


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
