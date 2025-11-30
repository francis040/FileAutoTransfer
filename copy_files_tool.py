import os
import shutil
import hashlib
import threading
import json
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import queue
import time


# ========== 配置部分 ==========
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
DEFAULT_SOURCE_DIR = r"D:\新建文件夹"
DEFAULT_TARGET_DIR = r"W:\下载\下载完"
LOG_FILE = os.path.join(os.path.dirname(__file__), "copy_log.txt")
HASH_DB_FILE = os.path.join(os.path.dirname(__file__), "file_hash.json")
# 检测模式: "fast" = 比较大小+修改时间（速度快，但对某些场景可能不够严格）
# "hash" = 计算 SHA256 哈希（更可靠，但会增加 IO）
DEFAULT_DETECTION_MODE = "fast"
# ============================


class ConfigManager:
    """配置管理器"""
    
    @staticmethod
    def load_config():
        """加载配置"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    # 向后兼容：确保检测模式存在
                    if "detection_mode" not in cfg:
                        cfg["detection_mode"] = DEFAULT_DETECTION_MODE
                    return cfg
            except:
                return {
                    "source_dir": DEFAULT_SOURCE_DIR,
                    "target_dir": DEFAULT_TARGET_DIR,
                    "detection_mode": DEFAULT_DETECTION_MODE,
                    "delete_after_copy": False
                }
        return {
            "source_dir": DEFAULT_SOURCE_DIR,
            "target_dir": DEFAULT_TARGET_DIR,
            "detection_mode": DEFAULT_DETECTION_MODE,
            "delete_after_copy": False
        }
    
    @staticmethod
    def save_config(config):
        """保存配置"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {str(e)}")


class FileCopyManager:
    """文件复制管理器"""
    
    def __init__(self, source_dir, target_dir, detection_mode=DEFAULT_DETECTION_MODE):
        self.pause_flag = False
        self.stop_flag = False
        self.delete_after_copy = False
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.detection_mode = detection_mode or DEFAULT_DETECTION_MODE
        self.hash_db = self.load_hash_db()
        
    def load_hash_db(self):
        """加载文件哈希数据库"""
        if os.path.exists(HASH_DB_FILE):
            try:
                with open(HASH_DB_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_hash_db(self):
        """保存文件哈希数据库"""
        try:
            with open(HASH_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.hash_db, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"保存哈希数据库失败: {str(e)}")
    
    def get_file_hash(self, file_path, chunk_size=8192):
        """计算文件哈希值"""
        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    sha256.update(data)
            return sha256.hexdigest()
        except Exception as e:
            self.log(f"计算哈希失败 {file_path}: {str(e)}")
            return None

    
    def log(self, message):
        """写入日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(log_message)
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_message + "\n")
        except Exception as e:
            print(f"写入日志失败: {str(e)}")
    
    def get_all_files(self, directory):
        """获取目录下的所有文件"""
        files = []
        try:
            for root, dirs, filenames in os.walk(directory):
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    files.append(file_path)
        except Exception as e:
            self.log(f"读取文件列表失败: {str(e)}")
        return files
    
    def should_copy_file(self, source_file, target_file):
        """判断是否需要复制文件

        快速模式（fast）: 先比较文件大小和修改时间，二者相同则视为相同。
        严格模式（hash）: 比较 SHA256 哈希（更可靠但更慢）。
        逻辑: 如果目标不存在 -> 复制；否则根据检测模式决定是否复制。
        """
        if not os.path.exists(target_file):
            return True, "新文件"

        try:
            s_stat = os.stat(source_file)
            t_stat = os.stat(target_file)
        except Exception as e:
            # 无法获取元数据时，退回到复制以保证一致性
            self.log(f"读取文件元数据失败: {e}")
            return True, "元数据读取失败"

        # 快速比较：大小+修改时间
        if self.detection_mode == "fast":
            # 使用整数化的时间比较以避免毫秒差异导致误判
            if s_stat.st_size == t_stat.st_size and int(s_stat.st_mtime) == int(t_stat.st_mtime):
                return False, "文件相同(大小+时间)"
            else:
                return True, "大小或时间不同"

        # 严格比较（hash）或其他情况：比较哈希
        source_hash = self.get_file_hash(source_file)
        target_hash = self.get_file_hash(target_file)
        if source_hash is None or target_hash is None:
            return True, "哈希计算失败，需复制"

        if source_hash != target_hash:
            return True, "文件已更新(哈希不同)"
        return False, "文件相同(哈希)"
    
    def copy_file_with_progress(self, source_file, target_file, progress_callback):
        """带进度的文件复制"""
        try:
            # 创建目标目录
            target_dir = os.path.dirname(target_file)
            os.makedirs(target_dir, exist_ok=True)
            
            # 获取文件大小
            file_size = os.path.getsize(source_file)

            # 复制文件并显示进度（带速度估算）
            with open(source_file, 'rb') as src, open(target_file, 'wb') as dst:
                chunk_size = 4 * 1024 * 1024  # 4MB chunks (更适合网络驱动器)
                copied = 0
                start_time = time.time()
                last_time = start_time
                while True:
                    # 在每次读写前检查暂停/停止标志，便于立即中断当前文件的复制
                    if getattr(self, 'pause_flag', False) or getattr(self, 'stop_flag', False):
                        # 关闭文件并删除不完整的目标文件
                        try:
                            dst.close()
                        except Exception:
                            pass
                        delete_error = None
                        try:
                            if os.path.exists(target_file):
                                os.remove(target_file)
                                self.log(f"已删除不完整目标文件: {target_file}")
                        except Exception as e:
                            delete_error = str(e)
                            self.log(f"删除不完整目标文件失败: {target_file} - {e}")
                        # 返回错误消息包含是否删除成功的信息
                        msg = "已取消: 用户暂停/停止"
                        if delete_error:
                            msg += f"(清理失败: {delete_error[:30]})"
                        return None, msg
                    
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    written = dst.write(chunk)
                    copied += written
                    now = time.time()
                    elapsed = now - last_time
                    # 以最近一次写入为基础估算速度（字节/秒）
                    speed = int(len(chunk) / elapsed) if elapsed > 0 else 0
                    last_time = now
                    progress = int((copied / file_size) * 100) if file_size > 0 else 100
                    # 尝试用 (progress, speed) 调用回调；如果回调只接受一个参数则回退
                    try:
                        progress_callback(progress, speed)
                    except TypeError:
                        try:
                            progress_callback(progress)
                        except Exception:
                            pass
            
            # 复制元数据（保留文件时间等）
            try:
                shutil.copystat(source_file, target_file)
            except Exception:
                # 非致命：继续执行，即使元数据复制失败
                pass

            # 写入循环结束后立即通知进度为 100%，以便 UI 及时切换到下一个文件
            try:
                progress_callback(100, 0)
            except TypeError:
                try:
                    progress_callback(100)
                except Exception:
                    pass

            # 仅在严格检测（hash）模式下计算并保存哈希，避免在网络盘上重复读取导致延迟
            if getattr(self, 'detection_mode', DEFAULT_DETECTION_MODE) == 'hash':
                file_hash = self.get_file_hash(target_file)
                if file_hash:
                    self.hash_db[target_file] = file_hash

            # 如果启用了复制后删除源文件，尝试删除源文件（仅在复制成功后）
            try:
                if getattr(self, 'delete_after_copy', False):
                    try:
                        os.remove(source_file)
                        self.log(f"已删除源文件: {source_file}")
                    except Exception as e:
                        self.log(f"删除源文件失败: {source_file} - {e}")
            except Exception:
                pass

            return True, "复制成功"
        except Exception as e:
            return False, str(e)
    
    def start_copy(self, progress_callback, status_callback, file_progress_callback, complete_callback):
        """启动复制过程"""
        self.pause_flag = False
        self.stop_flag = False
        
        self.log("=" * 50)
        self.log("开始文件增量复制")
        self.log(f"源目录: {self.source_dir}")
        self.log(f"目标目录: {self.target_dir}")
        
        # 获取所有源文件
        files = self.get_all_files(self.source_dir)
        total = len(files)
        
        if total == 0:
            self.log("源目录中没有文件")
            complete_callback("源目录中没有文件")
            return
        
        self.log(f"找到 {total} 个文件")
        
        copied_count = 0
        skipped_count = 0
        failed_count = 0
        
        for index, source_file in enumerate(files, 1):
            # 检查停止标志
            if self.stop_flag:
                self.log("用户停止了复制过程")
                complete_callback(f"已停止。复制: {copied_count}, 跳过: {skipped_count}, 失败: {failed_count}")
                return
            
            # 暂停功能
            while self.pause_flag:
                time.sleep(0.1)
            
            # 计算相对路径
            rel_path = os.path.relpath(source_file, self.source_dir)
            target_file = os.path.join(self.target_dir, rel_path)
            
            # 获取文件名
            file_name = os.path.basename(source_file)
            
            # 回调进度
            status_callback(f"处理中: {file_name}", source_file)
            progress_callback(int(index * 100 / total), f"{index}/{total}")
            
            # 检查是否需要复制
            should_copy, reason = self.should_copy_file(source_file, target_file)
            
            # 初始化文件进度 (progress, filename, reason, speed_bytes_per_sec)
            file_progress_callback(0, file_name, reason, 0)
            
            if should_copy:
                # 将文件级进度回调扩展为 (progress, speed)
                success, message = self.copy_file_with_progress(
                    source_file, target_file,
                    lambda p, s: file_progress_callback(p, file_name, reason, s)
                )
                if success is True:
                    # 复制成功
                    self.log(f"✓ 复制: {rel_path} ({reason})")
                    file_progress_callback(100, file_name, "✓ 完成", 0)
                    copied_count += 1
                elif success is None:
                    # 用户取消（success=None）：不计为失败，等待继续
                    self.log(f"⟲ 已取消: {rel_path} - {message}")
                    cancel_msg = "⟲ 已取消"
                    if "清理失败" in message:
                        cancel_msg = f"⟲ 已取消 (清理失败)"
                    file_progress_callback(0, file_name, cancel_msg, 0)
                    # 进入暂停等待状态，直到用户点击"继续"
                    while self.pause_flag and not self.stop_flag:
                        time.sleep(0.1)
                    # 如果用户停止了则直接退出
                    if self.stop_flag:
                        self.log("用户停止了复制过程")
                        complete_callback(f"已停止。复制: {copied_count}, 跳过: {skipped_count}, 失败: {failed_count}")
                        return
                    # 继续下一个文件（不计入任何计数）
                else:
                    # 真正的复制失败（success=False）
                    self.log(f"✗ 失败: {rel_path} - {message}")
                    file_progress_callback(0, file_name, f"✗ 失败: {message}", 0)
                    failed_count += 1
            else:
                self.log(f"⊘ 跳过: {rel_path} ({reason})")
                file_progress_callback(100, file_name, f"⊘ {reason}", 0)
                skipped_count += 1
        
        # 保存哈希数据库
        self.save_hash_db()
        
        # 完成
        self.log("=" * 50)
        self.log(f"复制完成 - 复制: {copied_count}, 跳过: {skipped_count}, 失败: {failed_count}")
        
        summary = f"复制完成！\n\n复制: {copied_count} 个文件\n跳过: {skipped_count} 个文件\n失败: {failed_count} 个文件\n\n日志文件: {LOG_FILE}"
        complete_callback(summary)


class FileCopyGUI:
    """文件复制GUI"""
    def __init__(self, root):
        self.root = root
        self.root.title("文件增量复制工具 v2.0")
        self.root.geometry("750x750")
        self.root.resizable(False, False)
        # 设置窗口始终在最前
        self.root.attributes('-topmost', True)
        # 加载配置
        self.config = ConfigManager.load_config()
        self.source_dir = self.config.get("source_dir", DEFAULT_SOURCE_DIR)
        self.target_dir = self.config.get("target_dir", DEFAULT_TARGET_DIR)
        self.manager = None
        self.copy_thread = None
        self.setup_ui()
    def setup_ui(self):
        """设置UI"""
        # 配置显示和选择
        config_frame = ttk.LabelFrame(self.root, text="配置信息", padding=10)
        config_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # 源目录
        source_frame = ttk.Frame(config_frame)
        source_frame.pack(fill=tk.X, pady=5)
        ttk.Label(source_frame, text="源目录:", width=10).pack(side=tk.LEFT)
        self.source_label = ttk.Label(source_frame, text=self.source_dir, foreground="blue", wraplength=400)
        self.source_label.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(source_frame, text="选择", command=self.select_source_dir, width=8).pack(side=tk.RIGHT, padx=5)
        
        # 目标目录
        target_frame = ttk.Frame(config_frame)
        target_frame.pack(fill=tk.X, pady=5)
        ttk.Label(target_frame, text="目标目录:", width=10).pack(side=tk.LEFT)
        self.target_label = ttk.Label(target_frame, text=self.target_dir, foreground="blue", wraplength=400)
        self.target_label.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(target_frame, text="选择", command=self.select_target_dir, width=8).pack(side=tk.RIGHT, padx=5)
        
        # 检测模式选择（fast: 大小+时间，hash: 哈希）
        detect_frame = ttk.Frame(config_frame)
        detect_frame.pack(fill=tk.X, pady=5)
        ttk.Label(detect_frame, text="检测模式:", width=10).pack(side=tk.LEFT)
        self.detect_var = tk.StringVar(value=self.config.get("detection_mode", DEFAULT_DETECTION_MODE))
        self.detect_box = ttk.Combobox(detect_frame, textvariable=self.detect_var, values=("fast","hash"), width=12, state="readonly")
        self.detect_box.pack(side=tk.LEFT, padx=5)
        self.detect_box.bind("<<ComboboxSelected>>", lambda e: self.change_detection_mode())
        ttk.Label(config_frame, text=f"日志文件: {LOG_FILE}", font=("Arial", 8)).pack(anchor=tk.W)
        
        # 状态显示
        status_frame = ttk.LabelFrame(self.root, text="复制状态", padding=10)
        status_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 整体进度
        ttk.Label(status_frame, text="整体进度:").pack(anchor=tk.W)
        self.overall_progress = ttk.Progressbar(status_frame, length=700, mode='determinate', maximum=100)
        self.overall_progress.pack(anchor=tk.W, padx=20, pady=5)
        self.overall_progress_label = ttk.Label(status_frame, text="0%", foreground="green")
        self.overall_progress_label.pack(anchor=tk.W, padx=20)
        
        # 当前文件
        ttk.Label(status_frame, text="当前文件:").pack(anchor=tk.W, pady=(10, 0))
        self.current_file_label = ttk.Label(status_frame, text="等待开始...", foreground="darkblue", font=("Arial", 9))
        self.current_file_label.pack(anchor=tk.W, padx=20)
        
        # 文件进度
        ttk.Label(status_frame, text="文件进度:").pack(anchor=tk.W, pady=(10, 0))
        self.file_progress = ttk.Progressbar(status_frame, length=700, mode='determinate', maximum=100)
        self.file_progress.pack(anchor=tk.W, padx=20, pady=5)
        self.file_progress_label = ttk.Label(status_frame, text="0%", foreground="darkgreen")
        self.file_progress_label.pack(anchor=tk.W, padx=20)
        
        # 传输速度显示
        ttk.Label(status_frame, text="当前速度:").pack(anchor=tk.W, pady=(8,0))
        self.speed_label = ttk.Label(status_frame, text="0 B/s", foreground="purple")
        self.speed_label.pack(anchor=tk.W, padx=20)
        
        # 状态消息
        ttk.Label(status_frame, text="状态消息:").pack(anchor=tk.W, pady=(10, 0))
        self.status_label = ttk.Label(status_frame, text="准备就绪", foreground="black")
        self.status_label.pack(anchor=tk.W, padx=20)
        
        # 按钮框
        button_frame = ttk.Frame(self.root)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.start_btn = ttk.Button(button_frame, text="开始复制", command=self.start_copy)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.pause_btn = ttk.Button(button_frame, text="暂停", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(button_frame, text="停止", command=self.stop_copy, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        self.open_log_btn = ttk.Button(button_frame, text="打开日志", command=self.open_log)
        self.open_log_btn.pack(side=tk.LEFT, padx=5)

        # 打开源/目标目录按钮
        self.open_source_btn = ttk.Button(button_frame, text="打开源目录", command=self.open_source_directory)
        self.open_source_btn.pack(side=tk.LEFT, padx=5)

        self.open_target_btn = ttk.Button(button_frame, text="打开目标目录", command=self.open_target_directory)
        self.open_target_btn.pack(side=tk.LEFT, padx=5)

        # 复制后删除源文件（默认不启用）
        self.delete_after_var = tk.BooleanVar(value=self.config.get("delete_after_copy", False))
        self.delete_after_chk = ttk.Checkbutton(button_frame, text="复制完成后删除源文件", variable=self.delete_after_var, command=self.toggle_delete_after)
        self.delete_after_chk.pack(side=tk.LEFT, padx=8)
        
        
        
        # 消息队列（用于线程间通信）
        self.queue = queue.Queue()
        self.check_queue()
        
    def select_source_dir(self):
        """选择源目录"""
        selected = filedialog.askdirectory(title="选择源目录", initialdir=self.source_dir)
        if selected:
            self.source_dir = selected
            self.source_label.config(text=selected)
            self.config["source_dir"] = selected
            ConfigManager.save_config(self.config)
    
    def select_target_dir(self):
        """选择目标目录"""
        selected = filedialog.askdirectory(title="选择目标目录", initialdir=self.target_dir)
        if selected:
            self.target_dir = selected
            self.target_label.config(text=selected)
            self.config["target_dir"] = selected
            ConfigManager.save_config(self.config)

    def toggle_delete_after(self):
        """切换复制后删除源文件选项并保存到配置"""
        self.config["delete_after_copy"] = bool(self.delete_after_var.get())
        ConfigManager.save_config(self.config)
    
    def change_detection_mode(self):
        """更改检测模式并保存到配置"""
        mode = self.detect_var.get()
        if mode not in ("fast", "hash"):
            return
        self.config["detection_mode"] = mode
        ConfigManager.save_config(self.config)
        
    def start_copy(self):
        """开始复制"""
        if not os.path.exists(self.source_dir):
            messagebox.showerror("错误", f"源目录不存在: {self.source_dir}")
            return
        
        if not os.path.exists(self.target_dir):
            try:
                os.makedirs(self.target_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"无法创建目标目录: {str(e)}")
                return
        
        # 从配置读取检测模式并传递给管理器
        detection_mode = self.config.get("detection_mode", DEFAULT_DETECTION_MODE)

        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL)

        self.overall_progress.config(value=0)
        self.overall_progress_label.config(text="0%")
        self.file_progress.config(value=0)
        self.file_progress_label.config(text="0%")
        self.current_file_label.config(text="正在启动...")
        self.status_label.config(text="复制中...")

        # 使用 Python 实现的复制（robocopy 功能已移除）
        self.manager = FileCopyManager(self.source_dir, self.target_dir, detection_mode=detection_mode)
        # 将删除源文件选项传递给管理器
        try:
            self.manager.delete_after_copy = bool(self.delete_after_var.get())
        except Exception:
            self.manager.delete_after_copy = False
        self.copy_thread = threading.Thread(target=self._copy_thread, daemon=True)
        self.copy_thread.start()
    
    def _copy_thread(self):
        """复制线程"""
        self.manager.start_copy(
            progress_callback=self._update_progress,
            status_callback=self._update_status,
            file_progress_callback=self._update_file_progress,
            complete_callback=self._copy_complete
        )

    
    
    def _update_progress(self, value, text):
        """更新总体进度"""
        self.queue.put(('progress', value, text))
    
    def _update_status(self, status, file_path):
        """更新状态"""
        self.queue.put(('status', status, file_path))
    
    def _update_file_progress(self, value, filename, reason, speed_bytes_per_sec):
        """更新文件进度（包含速度）"""
        self.queue.put(('file_progress', value, filename, reason, speed_bytes_per_sec))
    
    def _copy_complete(self, message):
        """复制完成"""
        self.queue.put(('complete', message))
    
    def check_queue(self):
        """检查消息队列"""
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == 'progress':
                    self.overall_progress.config(value=msg[1])
                    self.overall_progress_label.config(text=f"{msg[1]}% - {msg[2]}")
                elif msg[0] == 'status':
                    file_name = os.path.basename(msg[2])
                    self.current_file_label.config(text=file_name)
                    self.status_label.config(text=msg[1])
                elif msg[0] == 'file_progress':
                    # msg: ('file_progress', percent, filename, status, speed_bytes_per_sec)
                    percent = msg[1]
                    filename = msg[2]
                    status = msg[3]
                    speed_bps = msg[4] if len(msg) > 4 else 0
                    self.file_progress.config(value=percent)
                    self.file_progress_label.config(text=f"{percent}% - {status}")
                    # 更新速度显示
                    self.speed_label.config(text=self._format_speed(speed_bps))
                    # 当前文件名也确保展示
                    self.current_file_label.config(text=filename)
                
                elif msg[0] == 'complete':
                    self.start_btn.config(state=tk.NORMAL)
                    self.pause_btn.config(state=tk.DISABLED, text="暂停")
                    self.stop_btn.config(state=tk.DISABLED)
                    self.status_label.config(text="复制完成")
                    self.overall_progress.config(value=100)
                    self.overall_progress_label.config(text="100%")
                    self.file_progress.config(value=100)
                    self.file_progress_label.config(text="100% - 完成")
                    messagebox.showinfo("完成", msg[1])
        except queue.Empty:
            pass
        
        self.root.after(100, self.check_queue)

    def _format_speed(self, bps: int) -> str:
        """将字节/秒格式化为可读字符串"""
        try:
            bps = int(bps)
        except Exception:
            return "0 B/s"
        units = [(1<<30, 'GB/s'), (1<<20, 'MB/s'), (1<<10, 'KB/s')]
        for factor, suffix in units:
            if bps >= factor:
                return f"{bps/factor:.2f} {suffix}"
        return f"{bps} B/s"
    
    def toggle_pause(self):
        """暂停/继续 - 点击立即取消当前文件并进入暂停状态"""
        if self.manager is not None:
            if not self.manager.pause_flag:
                # 从复制中 -> 暂停：设置暂停标志以立即中断当前文件
                self.manager.pause_flag = True
                self.pause_btn.config(text="继续")
                self.status_label.config(text="已暂停 (当前文件已取消)", foreground="orange")
            else:
                # 从暂停 -> 继续：清除暂停标志，继续复制
                self.manager.pause_flag = False
                self.pause_btn.config(text="暂停")
                self.status_label.config(text="复制中...", foreground="black")
    
    def stop_copy(self):
        """停止复制"""
        if messagebox.askyesno("确认", "确定要停止复制吗？"):
            if self.manager is not None:
                self.manager.stop_flag = True
            
            self.start_btn.config(state=tk.NORMAL)
            self.pause_btn.config(state=tk.DISABLED, text="暂停")
            self.stop_btn.config(state=tk.DISABLED)
    
    def open_log(self):
        """打开日志文件"""
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            messagebox.showinfo("提示", "日志文件不存在")

    def open_source_directory(self):
        """在资源管理器中打开当前源目录"""
        path = getattr(self, 'source_dir', None)
        if not path:
            messagebox.showerror("错误", "源目录未设置")
            return
        if not os.path.exists(path):
            messagebox.showerror("错误", f"源目录不存在: {path}")
            return
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开源目录: {e}")

    def open_target_directory(self):
        """在资源管理器中打开当前目标目录"""
        path = getattr(self, 'target_dir', None)
        if not path:
            messagebox.showerror("错误", "目标目录未设置")
            return
        if not os.path.exists(path):
            messagebox.showerror("错误", f"目标目录不存在: {path}")
            return
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开目标目录: {e}")


def main():
    """主函数"""
    root = tk.Tk()
    gui = FileCopyGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
