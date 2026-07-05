# 文件增量复制工具（Modern Incremental Copy Tool）
一个基于 **PySide6** 开发的现代化 GUI 文件增量复制工具。  
它能够根据规则自动判断文件是否需要复制，并提供丝滑的暗色毛玻璃 UI、任务进度展示、传输组管理系统等功能。

---

## ✨ 特性（Features）

### 💡 增量复制
- 按需复制文件，而不是盲目全量复制  
- 支持两种检测模式：
  - `fast`：文件大小 + 修改时间对比  
  - `hash`：SHA256 文件哈希对比

---

### 🎨 现代深色 UI（Dark Glass Style）
- 使用 PySide6 自绘无边框窗口  
- 支持拖动、缩放、圆角、半透明毛玻璃风格  
- Mac 风格窗口按钮

---

### 📦 传输组管理系统（Presets）
可管理多个文件同步场景，例如：

- 下载目录 → NAS
- 视频素材 → 项目文件夹
- 摄影原片 → 备份盘  

支持：

- 新建传输组  
- 删除传输组  
- 应用到主界面  
- 自动保存到 `config.json`  

---

### 🚫 排除文件与文件夹
适合 BitComet 做种等场景，可在移动/复制时跳过指定内容：

- 排除单个文件
- 排除源目录下的某个文件夹及所有下层内容
- 排除规则跟随传输组保存
- 已删除或暂时不存在的排除项会保留，不影响任务运行

---

### 🪟 自定义深色弹窗
内置三种深色卡片式弹窗组件：

- 输入框：`NameInputDialog`
- 消息框：`CardMessageDialog`
- 询问框：`CardQuestionDialog`

全部视觉一致，替代系统默认白色窗体。

---

### 📊 实时进度展示
- 整体进度条  
- 当前文件进度条  
- 文件名、状态提示  
- 实时传输速度（B/s、KB/s、MB/s…）  

---

### 🧹 可选：复制完成后删除源文件
适合整理下载目录、移动素材文件等场景。

---

### 🧾 日志系统
所有操作都会记录在：

`copy_log.txt`

失败原因、跳过原因、速度信息都可追踪。

---

## 🖼 UI 预览

> 你可以在这里自行放入项目截图

---

## 📌 运行环境（Requirements）

- Python 3.8+
- PySide6  
- Windows（支持无边框自绘窗口行为；在 macOS / Linux 上可运行但效果可能不同）

安装依赖：

```bash
pip install PySide6
```

---

## 🚀 启动程序

```bash
python copy_files_tool.py
```

程序会自动生成：

- `config.json`（保存路径、传输组等）
- `file_hash.json`（哈希数据库）
- `copy_log.txt`（日志）

---

## 📁 配置文件说明（config.json）

示例：

```json
{
  "source_dir": "X:/XXX",
  "target_dir": "W:/XXX/XXX",
  "detection_mode": "fast",
  "delete_after_copy": false,
  "exclude_items": [
    {
      "type": "folder",
      "path": "正在做种"
    }
  ],
  "presets": [
    {
      "name": "XXX",
      "source": "X:/XXX",
      "target": "X:/XXX/XXX",
      "exclude_items": [
        {
          "type": "file",
          "path": "example.mkv"
        }
      ]
    }
  ]
}
```

---

## 🔧 构建为可执行文件（EXE）

推荐使用 **PyInstaller：**

```bash
pip install pyinstaller
```

打包：

```bash
pyinstaller -w -F --add-data "copy_files_tool.py;." copy_files_tool.py
```

打包后 EXE 会位于：

```
dist/copy_files_tool.exe
```

如果 UI 有毛玻璃效果，你可能需要加上 `--noconsole` 和图标文件。

---

## 🛠 技术栈（Tech Stack）

- PySide6  
- 自绘无边框窗口  
- 多线程复制任务  
- JSON 配置管理  
- 自定义深色卡片组件（Dialog）
