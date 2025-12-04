import os
import sys
import shutil
import hashlib
import threading
import json
from datetime import datetime
import time
import queue
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QPoint, QRect
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QProgressBar, QFrame,
    QComboBox, QMessageBox, QCheckBox, QSizePolicy,
    QDialog, QLineEdit
)

# ========== 配置部分 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DEFAULT_SOURCE_DIR = r"D:\新建文件夹"
DEFAULT_TARGET_DIR = r"W:\下载\下载完"
LOG_FILE = os.path.join(BASE_DIR, "copy_log.txt")
HASH_DB_FILE = os.path.join(BASE_DIR, "file_hash.json")
DEFAULT_DETECTION_MODE = "fast"  # fast=大小+时间；hash=计算哈希
# ============================


class ConfigManager:
    """配置管理：负责从 JSON 读取/写入路径和选项"""

    @staticmethod
    def load_config():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # 向后兼容：保证字段都在
                cfg.setdefault("detection_mode", DEFAULT_DETECTION_MODE)
                cfg.setdefault("delete_after_copy", False)
                cfg.setdefault("source_dir", DEFAULT_SOURCE_DIR)
                cfg.setdefault("target_dir", DEFAULT_TARGET_DIR)
                cfg.setdefault("presets", [])
                return cfg
            except Exception:
                pass
        # 默认配置
        return {
            "source_dir": DEFAULT_SOURCE_DIR,
            "target_dir": DEFAULT_TARGET_DIR,
            "detection_mode": DEFAULT_DETECTION_MODE,
            "delete_after_copy": False,
            "presets": [],
        }

    @staticmethod
    def save_config(config: dict):
        """保存配置到 config.json"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")


class FileCopyManager:
    """
    核心复制逻辑（纯逻辑，不依赖 UI）

    使用回调的方式把进度信息抛给 UI：
    - progress_callback(整体百分比, 文本)
    - status_callback(状态字符串, 当前文件绝对路径)
    - file_progress_callback(当前文件百分比, 文件名, 状态原因, 速度B/s)
    - complete_callback(最终总结文本)
    """

    def __init__(self, source_dir, target_dir, detection_mode=DEFAULT_DETECTION_MODE):
        self.pause_flag = False       # 控制“暂停”的标志位（由 UI 改）
        self.stop_flag = False        # 控制“停止”的标志位（由 UI 改）
        self.delete_after_copy = False  # 是否复制后删除源文件（由 UI 改）

        self.source_dir = source_dir
        self.target_dir = target_dir
        self.detection_mode = detection_mode or DEFAULT_DETECTION_MODE

        self.hash_db = self.load_hash_db()

    # ---------- 哈希数据库相关 ----------

    def load_hash_db(self):
        """从本地 JSON 载入哈希记录，用于严格模式减少重复计算"""
        if os.path.exists(HASH_DB_FILE):
            try:
                with open(HASH_DB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_hash_db(self):
        """把哈希记录写回文件"""
        try:
            with open(HASH_DB_FILE, "w", encoding="utf-8") as f:
                json.dump(self.hash_db, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"保存哈希数据库失败: {e}")

    # ---------- 工具函数 ----------

    def get_file_hash(self, file_path, chunk_size=8192):
        """计算单个文件的 SHA256 哈希"""
        try:
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            self.log(f"计算哈希失败 {file_path}: {e}")
            return None

    def log(self, message: str):
        """追加一行日志到日志文件，同时 print 出来"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # 日志写失败不影响主流程
            pass

    def get_all_files(self, directory: str):
        """递归获取目录下所有文件的绝对路径"""
        files = []
        for root, dirs, filenames in os.walk(directory):
            for name in filenames:
                files.append(os.path.join(root, name))
        return files

    # ---------- 判定是否需要复制 ----------

    def should_copy_file(self, source_file: str, target_file: str):
        """
        根据检测模式判断是否需要复制：
        - fast：只比较大小+修改时间
        - hash：比较哈希（更准确，但更慢）
        """
        if not os.path.exists(target_file):
            return True, "新文件"

        try:
            s_stat = os.stat(source_file)
            t_stat = os.stat(target_file)
        except Exception as e:
            self.log(f"读取文件元数据失败: {e}")
            return True, "元数据读取失败"

        if self.detection_mode == "fast":
            # 时间取整到秒，避免毫秒级差异导致误判
            if s_stat.st_size == t_stat.st_size and int(s_stat.st_mtime) == int(t_stat.st_mtime):
                return False, "文件相同(大小+时间)"
            return True, "大小或时间不同"

        # 严格模式：比较哈希
        src_hash = self.get_file_hash(source_file)
        dst_hash = self.get_file_hash(target_file)
        if src_hash is None or dst_hash is None:
            return True, "哈希计算失败，需复制"

        if src_hash != dst_hash:
            return True, "文件已更新(哈希不同)"
        return False, "文件相同(哈希)"

    # ---------- 单文件复制（带进度） ----------

    def copy_file_with_progress(self, source_file, target_file, progress_callback):
        """
        复制单个文件，并通过 progress_callback 汇报：
        progress_callback(百分比, 速度Bps)

        支持：
        - 暂停：当前文件复制会被中断并删除不完整目标
        - 停止：同上，但外层逻辑会退出整个任务
        """
        try:
            # 确保目标目录存在
            os.makedirs(os.path.dirname(target_file), exist_ok=True)

            file_size = os.path.getsize(source_file)
            chunk_size = 4 * 1024 * 1024  # 4MB 一块，适配网络盘

            # 检查目标是否已经存在（可能是部分文件）
            existing_size = 0
            if os.path.exists(target_file):
                try:
                    existing_size = os.path.getsize(target_file)
                except Exception:
                    existing_size = 0

            # 如果目标比源大，认为需要从头覆盖
            if existing_size > file_size:
                try:
                    os.remove(target_file)
                    existing_size = 0
                except Exception:
                    pass

            copied = 0
            start_time = time.time()
            last_time = start_time

            # 打开文件：如果存在部分文件，使用 r+b 并 seek 到末尾进行续传；否则新建写入
            src = open(source_file, "rb")
            try:
                if existing_size > 0:
                    try:
                        dst = open(target_file, "r+b")
                    except Exception:
                        # 无法以 r+b 打开就覆盖重建
                        dst = open(target_file, "wb")
                        existing_size = 0
                else:
                    dst = open(target_file, "wb")
            except Exception:
                src.close()
                raise

            try:
                # 如果要续传，从已有大小位置继续读写
                if existing_size > 0:
                    try:
                        src.seek(existing_size)
                        dst.seek(existing_size)
                    except Exception:
                        # 如果 seek 失败，则从头开始覆盖
                        src.seek(0)
                        dst.seek(0)
                        dst.truncate(0)
                        existing_size = 0

                # 复制循环
                while True:
                    # 优先处理停止请求
                    if self.stop_flag:
                        try:
                            dst.close()
                        except Exception:
                            pass
                        try:
                            if os.path.exists(target_file):
                                os.remove(target_file)
                                self.log(f"已删除不完整目标文件: {target_file}")
                        except Exception as e:
                            self.log(f"删除不完整目标文件失败: {target_file} - {e}")
                        return None, "已取消: 用户停止"

                    # 如果处于暂停状态，进入等待循环（保留已写入的数据以便续传）
                    if self.pause_flag:
                        # 在暂停期间，不调用 progress_callback 以免 UI 刷新异常；但可以短轮询
                        while self.pause_flag and not self.stop_flag:
                            time.sleep(0.1)
                        # 恢复后重置计时，避免速度计算异常
                        last_time = time.time()
                        if self.stop_flag:
                            # 停止的话删除不完整目标
                            try:
                                dst.close()
                            except Exception:
                                pass
                            try:
                                if os.path.exists(target_file):
                                    os.remove(target_file)
                                    self.log(f"已删除不完整目标文件: {target_file}")
                            except Exception as e:
                                self.log(f"删除不完整目标文件失败: {target_file} - {e}")
                            return None, "已取消: 用户停止"

                    chunk = src.read(chunk_size)
                    if not chunk:
                        break

                    written = dst.write(chunk)
                    dst.flush()
                    try:
                        os.fsync(dst.fileno())
                    except Exception:
                        # 某些文件系统或环境不支持 fsync，忽略
                        pass

                    copied += written

                    now = time.time()
                    elapsed = now - last_time
                    # 如果 elapsed 非常小，避免除以 0
                    speed = int(written / elapsed) if elapsed > 0.0001 else 0
                    last_time = now

                    total_copied = existing_size + copied
                    percent = int(total_copied * 100 / file_size) if file_size > 0 else 100
                    try:
                        progress_callback(percent, speed)
                    except Exception:
                        pass

                # 关闭文件句柄
                try:
                    dst.close()
                except Exception:
                    pass
                try:
                    src.close()
                except Exception:
                    pass

                # 尝试复制时间戳等元数据
                try:
                    shutil.copystat(source_file, target_file)
                except Exception:
                    pass

                # 最后一把拉到 100%
                try:
                    progress_callback(100, 0)
                except Exception:
                    pass

                # 严格模式下记录哈希
                if self.detection_mode == "hash":
                    dst_hash = self.get_file_hash(target_file)
                    if dst_hash:
                        self.hash_db[target_file] = dst_hash

                # 如果开启了“复制后删除源文件”
                if self.delete_after_copy:
                    try:
                        os.remove(source_file)
                        self.log(f"已删除源文件: {source_file}")
                    except Exception as e:
                        self.log(f"删除源文件失败: {source_file} - {e}")

                return True, "复制成功"

            except Exception as e:
                try:
                    dst.close()
                except Exception:
                    pass
                try:
                    src.close()
                except Exception:
                    pass
                return False, str(e)

        except Exception as e:
            return False, str(e)

    # ---------- 整体复制流程（在独立线程中运行） ----------

    def start_copy(self,
                   progress_callback,
                   status_callback,
                   file_progress_callback,
                   complete_callback):
        """
        主入口：遍历所有文件并逐个复制。
        这里不直接操作 UI，只通过回调把信息抛出去。
        """
        self.pause_flag = False
        self.stop_flag = False

        self.log("=" * 50)
        self.log("开始文件增量复制")
        self.log(f"源目录: {self.source_dir}")
        self.log(f"目标目录: {self.target_dir}")

        files = self.get_all_files(self.source_dir)
        total = len(files)

        if total == 0:
            self.log("源目录中没有文件")
            complete_callback("源目录中没有文件")
            return

        self.log(f"找到 {total} 个文件")

        copied = 0
        skipped = 0
        failed = 0

        for index, source_file in enumerate(files, 1):
            # 检查停止
            if self.stop_flag:
                self.log("用户停止了复制过程")
                complete_callback(
                    f"已停止。复制: {copied}, 跳过: {skipped}, 失败: {failed}"
                )
                return

            # 支持暂停：简单轮询
            while self.pause_flag and not self.stop_flag:
                time.sleep(0.1)

            rel_path = os.path.relpath(source_file, self.source_dir)
            target_file = os.path.join(self.target_dir, rel_path)
            file_name = os.path.basename(source_file)

            status_callback(f"处理中: {file_name}", source_file)
            progress_callback(int(index * 100 / total), f"{index}/{total}")

            should_copy, reason = self.should_copy_file(source_file, target_file)
            file_progress_callback(0, file_name, reason, 0)

            if should_copy:
                success, msg = self.copy_file_with_progress(
                    source_file, target_file,
                    lambda p, s: file_progress_callback(p, file_name, reason, s)
                )
                if success is True:
                    self.log(f"✓ 复制: {rel_path} ({reason})")
                    file_progress_callback(100, file_name, "✓ 完成", 0)
                    copied += 1
                elif success is None:
                    # 用户取消当前文件（暂停/停止）
                    self.log(f"⟲ 已取消: {rel_path} - {msg}")
                    file_progress_callback(0, file_name, "⟲ 已取消", 0)
                    # 这里不计入失败，交给外层逻辑处理 stop_flag
                else:
                    self.log(f"✗ 失败: {rel_path} - {msg}")
                    file_progress_callback(0, file_name, f"✗ 失败: {msg}", 0)
                    failed += 1
            else:
                self.log(f"⊘ 跳过: {rel_path} ({reason})")
                file_progress_callback(100, file_name, f"⊘ {reason}", 0)
                skipped += 1

        # 保存哈希数据库
        self.save_hash_db()

        self.log("=" * 50)
        self.log(f"复制完成 - 复制: {copied}, 跳过: {skipped}, 失败: {failed}")

        summary = (
            "复制完成！\n\n"
            f"复制: {copied} 个文件\n"
            f"跳过: {skipped} 个文件\n"
            f"失败: {failed} 个文件\n\n"
            f"日志文件: {LOG_FILE}"
        )
        complete_callback(summary)


# ======== 深色卡片式输入对话框 ========

class NameInputDialog(QDialog):
    """深色卡片风格的名称输入框，用来替代 QInputDialog.getText"""

    def __init__(self, parent, title: str, label_text: str, placeholder: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(title_label)

        label = QLabel(label_text)
        layout.addWidget(label)

        self.edit = QLineEdit()
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        layout.addWidget(self.edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_ok = QPushButton("确定")
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)

        layout.addLayout(btn_row)

    @staticmethod
    def get_text(parent, title: str, label_text: str, placeholder: str = ""):
        dlg = NameInputDialog(parent, title, label_text, placeholder)
        dlg.adjustSize()
        if parent is not None:
            g = parent.frameGeometry()
            rect = dlg.frameGeometry()
            rect.moveCenter(g.center())
            dlg.move(rect.topLeft())
        result = dlg.exec()
        text = dlg.edit.text()
        return text, (result == QDialog.Accepted)


# ======== 深色卡片式消息对话框（信息/错误/提示） ========

class CardMessageDialog(QDialog):
    """深色卡片风格的消息框，用于 OK 单按钮提示"""

    def __init__(self, parent, title: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(title_label)

        msg_label = QLabel(text)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    @staticmethod
    def show_message(parent, title: str, text: str):
        dlg = CardMessageDialog(parent, title, text)
        dlg.adjustSize()
        if parent is not None:
            g = parent.frameGeometry()
            rect = dlg.frameGeometry()
            rect.moveCenter(g.center())
            dlg.move(rect.topLeft())
        dlg.exec()


# ======== 深色卡片式“确认/取消”对话框 ========

class CardQuestionDialog(QDialog):
    """深色卡片风格的确认对话框，返回 True/False"""

    def __init__(self, parent, title: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(title_label)

        msg_label = QLabel(text)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_ok = QPushButton("确定")
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    @staticmethod
    def ask(parent, title: str, text: str) -> bool:
        dlg = CardQuestionDialog(parent, title, text)
        dlg.adjustSize()
        if parent is not None:
            g = parent.frameGeometry()
            rect = dlg.frameGeometry()
            rect.moveCenter(g.center())
            dlg.move(rect.topLeft())
        result = dlg.exec()
        return result == QDialog.Accepted


class PresetManagerDialog(QWidget):
    """传输组管理器弹窗"""

    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.setWindowTitle("传输组管理")
        # 弹窗 + 无边框 + 透明背景
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(420, 260)

        self.config = config
        self.presets = self.config.setdefault("presets", [])

        # 外层用一个卡片 Frame，复用主界面的 card 样式
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        title = QLabel("传输组管理")
        title.setObjectName("sectionTitle")
        # 标题固定左上角对齐
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        card_layout.addWidget(title)

        # 下拉列表展示已有的传输组
        self.combo = QComboBox()
        self._refresh_combo()
        card_layout.addWidget(self.combo)

        # 按钮区
        btn_row = QHBoxLayout()
        btn_new = QPushButton("新建传输组")
        btn_new.clicked.connect(self.create_new)
        btn_delete = QPushButton("删除当前")
        btn_delete.clicked.connect(self.delete_current)
        btn_apply = QPushButton("应用到主界面")
        btn_apply.clicked.connect(self.apply_current)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)

        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_delete)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)

        card_layout.addLayout(btn_row)

        outer_layout.addWidget(card)

    def _refresh_combo(self):
        """刷新下拉框内容"""
        self.combo.clear()
        if not self.presets:
            self.combo.addItem("（暂无传输组）")
            self.combo.setEnabled(False)
        else:
            self.combo.setEnabled(True)
            for p in self.presets:
                self.combo.addItem(p.get("name", "未命名"))

    def create_new(self):
        """新建一个传输组：输入名称 + 选择源目录 + 选择目标目录"""
        # 使用深色卡片式输入框
        name, ok = NameInputDialog.get_text(self, "新建传输组", "请输入传输组名称：")
        if not ok or not name.strip():
            return

        src = QFileDialog.getExistingDirectory(self, "选择源目录")
        if not src:
            return
        dst = QFileDialog.getExistingDirectory(self, "选择目标目录")
        if not dst:
            return

        preset = {
            "name": name.strip(),
            "source": src,
            "target": dst,
        }
        self.presets.append(preset)
        ConfigManager.save_config(self.config)
        self._refresh_combo()
        CardMessageDialog.show_message(self, "已保存", "传输组已保存。")

    def delete_current(self):
        """删除当前选中的传输组"""
        if not self.presets:
            return

        idx = self.combo.currentIndex()
        if idx < 0 or idx >= len(self.presets):
            return

        p = self.presets[idx]
        name = p.get("name", "未命名")
        if not CardQuestionDialog.ask(self, "确认删除", f"确定要删除传输组：{name} ？"):
            return

        self.presets.pop(idx)
        ConfigManager.save_config(self.config)
        self._refresh_combo()

    def apply_current(self):
        """把当前传输组应用到主界面"""
        if not self.presets:
            return
        idx = self.combo.currentIndex()
        if idx < 0 or idx >= len(self.presets):
            return

        p = self.presets[idx]
        parent = self.parent()
        if isinstance(parent, ModernWindow):
            parent.apply_preset(p)
            CardMessageDialog.show_message(self, "已应用", "传输组已应用到主界面。")


class ModernWindow(QMainWindow):
    """PySide6 深色玻璃 UI"""

    def __init__(self):
        super().__init__()

        # --- 状态 / 配置 ---
        self.config = ConfigManager.load_config()
        self.source_dir = self.config.get("source_dir", DEFAULT_SOURCE_DIR)
        self.target_dir = self.config.get("target_dir", DEFAULT_TARGET_DIR)
        self.manager: Optional[FileCopyManager] = None
        self.copy_thread: Optional[threading.Thread] = None

        # 子线程 -> UI 的消息队列
        self.queue: "queue.Queue[tuple]" = queue.Queue()

        # 拖动无边框窗口相关
        self._dragging = False
        self._drag_pos = QPoint()

        # 无边框窗口自定义缩放相关
        self._resizing = False          # 当前是否处于“拉伸窗口”状态
        self._resize_edge = ""          # 命中的是哪个边/角（l r t b 组合）
        self._resize_start_geom = QRect()   # 开始拖动时的窗口几何
        self._resize_start_pos = QPoint()   # 开始拖动时鼠标的全局坐标
        self._resize_margin = 8         # 距离边缘多少像素算“可拉伸区域”
        self._min_width = 700
        self._min_height = 450

        self._preset_dialog: Optional[PresetManagerDialog] = None

        # --- UI 初始化 ---
        self._setup_window_flags()
        self._setup_style()
        self._build_ui()

        # 定时从队列读取子线程发来的消息，更新 UI
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_queue)
        self.timer.start(80)

    # ---------- 窗口基础设置 ----------

    def _setup_window_flags(self):
        self.setWindowTitle("文件增量复制工具 v2.0 - Modern Dark")
        self.resize(820, 640)

        # 无边框 + 允许出现在任务栏
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
        )

        # 允许背景透明，用于做圆角玻璃
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_style(self):
        """全局样式表（深色+卡片+按钮+进度条）"""
        self.setStyleSheet(
            """
            QMainWindow {
                background: transparent;
            }
            QWidget#root {
                background-color: rgba(20, 20, 20, 230);
                border-radius: 18px;
            }
            QFrame#titleBar {
                background-color: transparent;
            }
            QLabel {
                color: #e0e0e0;
                font-size: 13px;
            }
            QLabel#titleLabel {
                font-size: 14px;
                font-weight: 600;
                color: #f5f5f5;
            }
            QLabel#sectionTitle {
                font-size: 13px;
                font-weight: 600;
                color: #f0f0f0;
            }
            QFrame#card {
                background-color: rgba(40, 40, 40, 220);
                border-radius: 14px;
                border: 1px solid rgba(80, 80, 80, 200);
            }
            QPushButton {
                background-color: #3a3a3a;
                border-radius: 8px;
                padding: 8px 16px;
                border: 1px solid #4a4a4a;
                color: #f0f0f0;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:pressed {
                background-color: #2f2f2f;
            }
            QPushButton:disabled {
                background-color: #2b2b2b;
                color: #777;
                border-color: #333;
            }
            QProgressBar {
                background-color: #2b2b2b;
                border-radius: 8px;
                height: 14px;
                text-align: center;
                color: #cfcfcf;
                font-size: 11px;
                border: 1px solid #3a3a3a;
            }
            QProgressBar::chunk {
                background-color: #4aa3ff;
                border-radius: 8px;
            }
            QComboBox {
                background-color: #2f2f2f;
                padding: 4px 10px;
                border-radius: 6px;
                border: 1px solid #4a4a4a;
                color: #f0f0f0;
            }
            QComboBox QAbstractItemView {
                background-color: #2b2b2b;
                border-radius: 6px;
                selection-background-color: #4aa3ff;
                color: #f0f0f0;
            }
            QCheckBox {
                color: #d0d0d0;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            QCheckBox::indicator:unchecked {
                border-radius: 3px;
                border: 1px solid #666;
                background: #2c2c2c;
            }
            QCheckBox::indicator:checked {
                border-radius: 3px;
                background: #4aa3ff;
                border: 1px solid #4aa3ff;
            }

            /* 右上角三个圆点按钮（颜色保留 mac 风格） */
            QPushButton#closeButton {
                background-color: #ff5f57;
                border-radius: 6px;
                border: none;
            }
            QPushButton#closeButton:hover {
                background-color: #ff7b72;
            }
            QPushButton#minButton {
                background-color: #febc2e;
                border-radius: 6px;
                border: none;
            }
            QPushButton#minButton:hover {
                background-color: #f8d061;
            }
            QPushButton#maxButton {
                background-color: #28c840;
                border-radius: 6px;
                border: none;
            }
            QPushButton#maxButton:hover {
                background-color: #42d25c;
            }

            /* 只重写 QMessageBox 内 Label 和 Button 的颜色（以防万一还有地方用了） */
            QMessageBox QLabel {
                color: black;
                font-size: 14px;
            }
            QMessageBox QPushButton {
                color: black;
                background-color: #e0e0e0;
                padding: 6px 14px;
                border-radius: 6px;
            }
            QMessageBox QPushButton:hover {
                background-color: #d5d5d5;
            }
            """
        )

    def _build_ui(self):
        """构造界面布局"""

        root = QWidget()
        root.setObjectName("root")
        # 根容器随窗口扩展
        root.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 14, 16, 16)
        main_layout.setSpacing(10)

        # ---------- 自定义标题栏 ----------
        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        # 固定高度，水平扩展，保证一直在顶部
        title_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title_bar.setFixedHeight(44)

        t_layout = QHBoxLayout(title_bar)
        t_layout.setContentsMargins(8, 6, 8, 6)
        t_layout.setSpacing(8)

        # 左侧：标题文本（固定左上角）
        title_label = QLabel("文件增量复制工具 v2.0")
        title_label.setObjectName("titleLabel")
        t_layout.addWidget(title_label)
        t_layout.addStretch()

        # 右侧：三个圆点按钮（固定右上角）
        btn_min = QPushButton()
        btn_min.setObjectName("minButton")
        btn_min.setFixedSize(12, 12)
        btn_min.clicked.connect(self.showMinimized)

        btn_max = QPushButton()
        btn_max.setObjectName("maxButton")
        btn_max.setFixedSize(12, 12)
        btn_max.clicked.connect(self._toggle_max_restore)

        btn_close = QPushButton()
        btn_close.setObjectName("closeButton")
        btn_close.setFixedSize(12, 12)
        btn_close.clicked.connect(self.close)

        t_layout.addWidget(btn_min)
        t_layout.addWidget(btn_max)
        t_layout.addWidget(btn_close)

        self.title_bar = title_bar
        main_layout.addWidget(title_bar)

        # ---------- 内容区域 ----------
        content_wrapper = QWidget()
        content_wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cw_layout = QVBoxLayout(content_wrapper)
        cw_layout.setContentsMargins(4, 4, 4, 4)
        cw_layout.setSpacing(12)

        # === 配置卡片 ===
        config_card = QFrame()
        config_card.setObjectName("card")
        config_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cfg_layout = QVBoxLayout(config_card)
        cfg_layout.setContentsMargins(16, 14, 16, 14)
        cfg_layout.setSpacing(10)

        cfg_title = QLabel("配置信息")
        cfg_title.setObjectName("sectionTitle")
        cfg_layout.addWidget(cfg_title)

        # 传输组管理按钮
        self.btn_manage_presets = QPushButton("管理传输组…")
        self.btn_manage_presets.setFixedWidth(110)
        self.btn_manage_presets.clicked.connect(self.open_preset_manager)
        cfg_layout.addWidget(self.btn_manage_presets)

        # 源目录
        row1 = QHBoxLayout()
        lbl_src = QLabel("源目录：")
        self.lbl_src_path = QLabel(self.source_dir or "未选择")
        self.lbl_src_path.setStyleSheet("color:#9bd5ff;")
        self.lbl_src_path.setWordWrap(True)
        btn_src = QPushButton("选择")
        btn_src.setFixedWidth(70)
        btn_src.clicked.connect(self.select_source_dir)

        row1.addWidget(lbl_src)
        row1.addWidget(self.lbl_src_path, 1)
        row1.addWidget(btn_src)

        # 目标目录
        row2 = QHBoxLayout()
        lbl_dst = QLabel("目标目录：")
        self.lbl_dst_path = QLabel(self.target_dir or "未选择")
        self.lbl_dst_path.setStyleSheet("color:#9bd5ff;")
        self.lbl_dst_path.setWordWrap(True)
        btn_dst = QPushButton("选择")
        btn_dst.setFixedWidth(70)
        btn_dst.clicked.connect(self.select_target_dir)

        row2.addWidget(lbl_dst)
        row2.addWidget(self.lbl_dst_path, 1)
        row2.addWidget(btn_dst)

        # 检测模式
        row3 = QHBoxLayout()
        lbl_mode = QLabel("检测模式：")
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["fast", "hash"])
        self.combo_mode.setCurrentText(
            self.config.get("detection_mode", DEFAULT_DETECTION_MODE)
        )
        self.combo_mode.currentTextChanged.connect(self.on_mode_changed)

        row3.addWidget(lbl_mode)
        row3.addWidget(self.combo_mode, 0)
        row3.addStretch()

        lbl_log = QLabel(f"日志文件: {LOG_FILE}")
        lbl_log.setStyleSheet("color:#888888;font-size:11px;")

        cfg_layout.addLayout(row1)
        cfg_layout.addLayout(row2)
        cfg_layout.addLayout(row3)
        cfg_layout.addWidget(lbl_log)

        # === 状态卡片 ===
        status_card = QFrame()
        status_card.setObjectName("card")
        status_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        st_layout = QVBoxLayout(status_card)
        st_layout.setContentsMargins(16, 14, 16, 14)
        st_layout.setSpacing(10)

        st_title = QLabel("复制状态")
        st_title.setObjectName("sectionTitle")
        st_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        st_layout.addWidget(st_title)

        self.overall_label = QLabel("整体进度：0%")
        self.overall_bar = QProgressBar()
        self.overall_bar.setValue(0)

        self.current_file_label = QLabel("当前文件：等待开始...")
        self.file_progress = QProgressBar()
        self.file_progress.setValue(0)
        self.file_progress_label = QLabel("文件进度：0%")

        self.speed_label = QLabel("当前速度：0 B/s")
        self.status_label = QLabel("状态消息：准备就绪")

        st_layout.addWidget(self.overall_label)
        st_layout.addWidget(self.overall_bar)
        st_layout.addSpacing(6)
        st_layout.addWidget(self.current_file_label)
        st_layout.addWidget(self.file_progress)
        st_layout.addWidget(self.file_progress_label)
        st_layout.addSpacing(6)
        st_layout.addWidget(self.speed_label)
        st_layout.addWidget(self.status_label)

        cw_layout.addWidget(config_card)
        cw_layout.addWidget(status_card)

        # === 按钮行 ===
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_start = QPushButton("开始复制")
        self.btn_start.clicked.connect(self.start_copy)

        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_copy)

        self.btn_log = QPushButton("打开日志")
        self.btn_log.clicked.connect(self.open_log)

        self.btn_open_src = QPushButton("打开源目录")
        self.btn_open_src.clicked.connect(self.open_source_directory)

        self.btn_open_dst = QPushButton("打开目标目录")
        self.btn_open_dst.clicked.connect(self.open_target_directory)

        self.chk_delete_after = QCheckBox("复制完成后删除源文件")
        self.chk_delete_after.setChecked(
            self.config.get("delete_after_copy", False)
        )
        self.chk_delete_after.stateChanged.connect(self.on_delete_after_changed)

        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_pause)
        btn_row.addWidget(self.btn_stop)
        btn_row.addSpacing(16)
        btn_row.addWidget(self.btn_log)
        btn_row.addWidget(self.btn_open_src)
        btn_row.addWidget(self.btn_open_dst)
        btn_row.addStretch()
        btn_row.addWidget(self.chk_delete_after)

        cw_layout.addLayout(btn_row)

        # 内容区占据剩余空间（标题栏高度固定）
        main_layout.addWidget(content_wrapper, 1)

        try:
            main_layout.setStretch(main_layout.indexOf(title_bar), 0)
            main_layout.setStretch(main_layout.indexOf(content_wrapper), 1)
        except Exception:
            pass

    # ---------- 命中边缘的判定 & 光标形状 ----------

    def _hit_test_edges(self, pos: QPoint) -> str:
        """根据鼠标在窗口内的位置，判断命中哪个边/角（l r t b 组合）"""
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        m = self._resize_margin

        edge = ""

        if x <= m:
            edge += "l"
        elif x >= w - m:
            edge += "r"

        if y <= m:
            edge += "t"
        elif y >= h - m:
            edge += "b"

        return edge

    def _update_cursor_by_edge(self, edge: str):
        """根据命中的边/角设置鼠标样式"""
        if not edge:
            self.unsetCursor()
            return

        if ("l" in edge and "t" in edge) or ("r" in edge and "b" in edge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif ("r" in edge and "t" in edge) or ("l" in edge and "b" in edge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif "l" in edge or "r" in edge:
            self.setCursor(Qt.SizeHorCursor)
        elif "t" in edge or "b" in edge:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    # ---------- 标题栏拖动 + 边缘缩放 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()

            # 先判断是否命中可缩放边缘
            edge = self._hit_test_edges(pos)
            if edge:
                self._resizing = True
                self._resize_edge = edge
                self._resize_start_geom = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
                event.accept()
                return

            # 如果没命中边缘，再判断是否在标题栏区域 -> 拖动窗口
            if event.position().y() <= self.title_bar.height() + 10:
                self._dragging = True
                self._drag_pos = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()

        if self._resizing:
            # 正在缩放窗口
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            rect = QRect(self._resize_start_geom)

            min_w = self._min_width
            min_h = self._min_height

            if "l" in self._resize_edge:
                new_left = rect.left() + delta.x()
                if rect.right() - new_left >= min_w:
                    rect.setLeft(new_left)
            elif "r" in self._resize_edge:
                new_right = rect.right() + delta.x()
                if new_right - rect.left() >= min_w:
                    rect.setRight(new_right)

            if "t" in self._resize_edge:
                new_top = rect.top() + delta.y()
                if rect.bottom() - new_top >= min_h:
                    rect.setTop(new_top)
            elif "b" in self._resize_edge:
                new_bottom = rect.bottom() + delta.y()
                if new_bottom - rect.top() >= min_h:
                    rect.setBottom(new_bottom)

            self.setGeometry(rect)
            event.accept()
            return

        if self._dragging and event.buttons() & Qt.LeftButton:
            # 拖动窗口
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return

        # 没有在拖动/缩放时，更新光标形状
        edge = self._hit_test_edges(pos)
        self._update_cursor_by_edge(edge)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            if self._resizing:
                self._resizing = False
                self._resize_edge = ""
                self.unsetCursor()

        super().mouseReleaseEvent(event)

    def _toggle_max_restore(self):
        """最大化 / 还原 切换"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ---------- 配置项改变 & 传输组管理 ----------

    def open_preset_manager(self):
        """打开传输组管理器"""
        if self._preset_dialog is None or not self._preset_dialog.isVisible():
            self._preset_dialog = PresetManagerDialog(self, self.config)
            # 简单居中到主窗口附近
            geo = self.frameGeometry()
            dlg_geo = self._preset_dialog.frameGeometry()
            center = geo.center()
            dlg_geo.moveCenter(center)
            self._preset_dialog.move(dlg_geo.topLeft())
            self._preset_dialog.show()
        else:
            self._preset_dialog.raise_()
            self._preset_dialog.activateWindow()

    def apply_preset(self, preset: dict):
        """把一个传输组应用到主界面"""
        self.source_dir = preset.get("source", "") or ""
        self.target_dir = preset.get("target", "") or ""
        self.lbl_src_path.setText(self.source_dir or "未选择")
        self.lbl_dst_path.setText(self.target_dir or "未选择")
        self.config["source_dir"] = self.source_dir
        self.config["target_dir"] = self.target_dir
        ConfigManager.save_config(self.config)

    def select_source_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择源目录", self.source_dir or ""
        )
        if path:
            self.source_dir = path
            self.lbl_src_path.setText(path)
            self.config["source_dir"] = path
            ConfigManager.save_config(self.config)

    def select_target_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择目标目录", self.target_dir or ""
        )
        if path:
            self.target_dir = path
            self.lbl_dst_path.setText(path)
            self.config["target_dir"] = path
            ConfigManager.save_config(self.config)

    def on_mode_changed(self, mode: str):
        self.config["detection_mode"] = mode
        ConfigManager.save_config(self.config)

    def on_delete_after_changed(self, state: int):
        self.config["delete_after_copy"] = (state == Qt.Checked)
        ConfigManager.save_config(self.config)

    # ---------- 复制流程控制 ----------

    def start_copy(self):
        """开始复制按钮"""
        if not self.source_dir or not os.path.exists(self.source_dir):
            CardMessageDialog.show_message(self, "错误", f"源目录不存在: {self.source_dir}")
            return

        if not self.target_dir:
            CardMessageDialog.show_message(self, "错误", "目标目录未设置")
            return

        if not os.path.exists(self.target_dir):
            try:
                os.makedirs(self.target_dir, exist_ok=True)
            except Exception as e:
                CardMessageDialog.show_message(self, "错误", f"无法创建目标目录: {e}")
                return

        detection_mode = self.config.get(
            "detection_mode", DEFAULT_DETECTION_MODE
        )

        # 按钮状态
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停")
        self.btn_stop.setEnabled(True)

        # 初始化进度显示
        self.overall_bar.setValue(0)
        self.overall_label.setText("整体进度：0%")
        self.file_progress.setValue(0)
        self.file_progress_label.setText("文件进度：0%")
        self.current_file_label.setText("当前文件：正在启动...")
        self.status_label.setText("状态消息：复制中...")
        self.speed_label.setText("当前速度：0 B/s")

        # 创建 FileCopyManager，并把配置传进去
        self.manager = FileCopyManager(
            self.source_dir, self.target_dir, detection_mode
        )
        self.manager.delete_after_copy = self.config.get(
            "delete_after_copy", False
        )

        # 开新线程执行复制，避免卡 UI
        self.copy_thread = threading.Thread(
            target=self._copy_thread_func,
            daemon=True,
        )
        self.copy_thread.start()

    def _copy_thread_func(self):
        """在后台线程中运行复制流程"""
        if self.manager is not None:
            self.manager.start_copy(
                progress_callback=self._update_progress,
                status_callback=self._update_status,
                file_progress_callback=self._update_file_progress,
                complete_callback=self._copy_complete,
            )

    # 以下几个函数在子线程中被调用，只负责向队列塞消息，不直接操作 UI
    def _update_progress(self, value, text):
        self.queue.put(("progress", value, text))

    def _update_status(self, status, file_path):
        self.queue.put(("status", status, file_path))

    def _update_file_progress(self, value, filename, reason, speed_bps):
        self.queue.put(("file_progress", value, filename, reason, speed_bps))

    def _copy_complete(self, message):
        self.queue.put(("complete", message))

    # ---------- 在主线程中定时处理队列，更新 UI ----------
    def process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                mtype = msg[0]

                if mtype == "progress":
                    value, text = msg[1], msg[2]
                    self.overall_bar.setValue(value)
                    self.overall_label.setText(
                        f"整体进度：{value}% ({text})"
                    )

                elif mtype == "status":
                    status_text, file_path = msg[1], msg[2]
                    name = os.path.basename(file_path) if file_path else ""
                    self.current_file_label.setText(
                        f"当前文件：{name}"
                    )
                    self.status_label.setText(
                        f"状态消息：{status_text}"
                    )

                elif mtype == "file_progress":
                    percent, filename, reason, speed_bps = (
                        msg[1],
                        msg[2],
                        msg[3],
                        msg[4],
                    )
                    self.file_progress.setValue(percent)
                    self.file_progress_label.setText(
                        f"文件进度：{percent}% - {reason}"
                    )
                    self.current_file_label.setText(
                        f"当前文件：{filename}"
                    )
                    self.speed_label.setText(
                        f"当前速度：{self._format_speed(speed_bps)}"
                    )

                elif mtype == "complete":
                    msg_text = msg[1]
                    self.btn_start.setEnabled(True)
                    self.btn_pause.setEnabled(False)
                    self.btn_pause.setText("暂停")
                    self.btn_stop.setEnabled(False)

                    self.status_label.setText("状态消息：复制完成")
                    self.overall_bar.setValue(100)
                    self.overall_label.setText("整体进度：100%")
                    self.file_progress.setValue(100)
                    self.file_progress_label.setText(
                        "文件进度：100% - 完成"
                    )

                    # 使用深色卡片式消息框替代默认白色提示框
                    CardMessageDialog.show_message(self, "完成", msg_text)

        except queue.Empty:
            pass

    @staticmethod
    def _format_speed(bps: int) -> str:
        """把 B/s 转成更友好的字符串"""
        try:
            bps = int(bps)
        except Exception:
            return "0 B/s"
        units = [(1 << 30, "GB/s"), (1 << 20, "MB/s"), (1 << 10, "KB/s")]
        for factor, suffix in units:
            if bps >= factor:
                return f"{bps / factor:.2f} {suffix}"
        return f"{bps} B/s"

    def toggle_pause(self):
        """暂停 / 继续 按钮"""
        if not self.manager:
            return
        if not self.manager.pause_flag:
            self.manager.pause_flag = True
            self.btn_pause.setText("继续")
            self.status_label.setText("状态消息：已暂停（当前文件已取消）")
        else:
            self.manager.pause_flag = False
            self.btn_pause.setText("暂停")
            self.status_label.setText("状态消息：复制中...")

    def stop_copy(self):
        """停止按钮"""
        if not self.manager:
            return
        if not CardQuestionDialog.ask(self, "确认", "确定要停止复制吗？"):
            return
        self.manager.stop_flag = True
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停")
        self.btn_stop.setEnabled(False)

    # ---------- 打开日志 / 目录 ----------

    def open_log(self):
        if os.path.exists(LOG_FILE):
            try:
                os.startfile(LOG_FILE)
            except Exception as e:
                CardMessageDialog.show_message(self, "错误", f"无法打开日志文件: {e}")
        else:
            CardMessageDialog.show_message(self, "提示", "日志文件不存在")

    def open_source_directory(self):
        path = self.source_dir
        if not path:
            CardMessageDialog.show_message(self, "错误", "源目录未设置")
            return
        if not os.path.exists(path):
            CardMessageDialog.show_message(self, "错误", f"源目录不存在: {path}")
            return
        try:
            os.startfile(path)
        except Exception as e:
            CardMessageDialog.show_message(self, "错误", f"无法打开源目录: {e}")

    def open_target_directory(self):
        path = self.target_dir
        if not path:
            CardMessageDialog.show_message(self, "错误", "目标目录未设置")
            return
        if not os.path.exists(path):
            CardMessageDialog.show_message(self, "错误", f"目标目录不存在: {path}")
            return
        try:
            os.startfile(path)
        except Exception as e:
            CardMessageDialog.show_message(self, "错误", f"无法打开目标目录: {e}")


def main():
    app = QApplication(sys.argv)
    win = ModernWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
