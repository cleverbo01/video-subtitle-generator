from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "project_config.json"
TITLE = "视频字幕生成器 - 首次配置与环境检测"


def enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def candidate_whisper_homes(config):
    values = [
        os.environ.get("WHISPER_GPT_HOME"),
        config.get("whisper_home"),
        APP_DIR / "Whisper-GPT",
        APP_DIR.parent / "Whisper-GPT",
        APP_DIR.parent.parent / "Whisper-GPT",
    ]
    seen = set()
    for value in values:
        if not value:
            continue
        path = Path(value).expanduser()
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            yield path


def resolve_whisper_home(config):
    for path in candidate_whisper_homes(config):
        if (path / "scripts" / "transcribe.py").is_file():
            return path.resolve()
    return None


def hidden_run(command, **kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        kwargs.setdefault("startupinfo", info)
    return subprocess.run(command, **kwargs)


class SetupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        if os.name == "nt":
            self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        self.title(TITLE)
        self.geometry("940x690")
        self.minsize(820, 600)
        self.config_data = load_config()
        self.status_vars = {}
        self.vars = {
            "whisper_home": tk.StringVar(value=str(self.config_data.get("whisper_home", ""))),
            "ffmpeg_path": tk.StringVar(value=str(self.config_data.get("ffmpeg_path", ""))),
            "base_url": tk.StringVar(value=str(self.config_data.get("base_url", ""))),
            "model": tk.StringVar(value=str(self.config_data.get("model", ""))),
            "api_key": tk.StringVar(),
        }
        self._build_ui()
        self.after(200, self.run_detection)

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"), foreground="#17365D")
        style.configure("Hint.TLabel", foreground="#5D6B82")
        style.configure("Card.TLabelframe", background="#F8FAFD")
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"), foreground="#17365D")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), foreground="white", background="#2563EB")
        style.map("Accent.TButton", background=[("active", "#1D4ED8")])

        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        ttk.Label(root, text="首次配置与环境检测", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            root,
            text="检查 GPU、FFmpeg、Whisper-GPT 与 API。所有路径可重新选择，项目移动后再次检测即可恢复。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 14))

        paths = ttk.LabelFrame(root, text="本机路径", style="Card.TLabelframe", padding=12)
        paths.grid(row=2, column=0, sticky="ew")
        paths.columnconfigure(1, weight=1)
        self._path_row(paths, 0, "Whisper-GPT 文件夹", "whisper_home", self.pick_whisper)
        self._path_row(paths, 1, "FFmpeg.exe（可选）", "ffmpeg_path", self.pick_ffmpeg)

        checks = ttk.LabelFrame(root, text="检测结果", style="Card.TLabelframe", padding=12)
        checks.grid(row=3, column=0, sticky="nsew", pady=12)
        checks.columnconfigure(1, weight=1)
        for row, key in enumerate(("项目路径", "Whisper-GPT", "Python 环境", "FFmpeg / FFprobe", "GPU / CUDA", "Whisper 模型目录")):
            ttk.Label(checks, text=key, width=18).grid(row=row, column=0, sticky="w", pady=4)
            variable = tk.StringVar(value="等待检测…")
            self.status_vars[key] = variable
            ttk.Label(checks, textvariable=variable, wraplength=620).grid(row=row, column=1, sticky="w", pady=4)

        api = ttk.LabelFrame(root, text="中转站 API 连通性（可选）", style="Card.TLabelframe", padding=12)
        api.grid(row=4, column=0, sticky="ew")
        api.columnconfigure(1, weight=1)
        ttk.Label(api, text="Base URL").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(api, textvariable=self.vars["base_url"]).grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(api, text="API Key").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(api, textvariable=self.vars["api_key"], show="●").grid(row=1, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(api, text="模型（可选）").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(api, textvariable=self.vars["model"]).grid(row=2, column=1, sticky="ew", padx=8, pady=3)
        self.api_status = tk.StringVar(value="未测试")
        ttk.Label(api, textvariable=self.api_status, style="Hint.TLabel").grid(row=0, column=2, rowspan=2, sticky="w", padx=8)
        ttk.Button(api, text="测试 API", command=self.test_api).grid(row=2, column=2, padx=8)

        actions = ttk.Frame(root)
        actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="重新检测", command=self.run_detection).pack(side="left")
        ttk.Button(actions, text="FFmpeg 下载页", command=lambda: webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")).pack(side="left", padx=8)
        ttk.Button(actions, text="下载 large-v3 模型", command=self.download_model).pack(side="left")
        ttk.Button(actions, text="安装 Whisper-GPT 依赖", command=self.install_requirements).pack(side="left")
        ttk.Button(actions, text="保存配置", style="Accent.TButton", command=self.save_config).pack(side="right")

    def _path_row(self, parent, row, label, key, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(parent, text="选择…", command=command).grid(row=row, column=2, pady=4)

    def pick_whisper(self):
        value = filedialog.askdirectory(title="选择 Whisper-GPT 文件夹")
        if value:
            self.vars["whisper_home"].set(value)
            self.run_detection()

    def pick_ffmpeg(self):
        value = filedialog.askopenfilename(title="选择 ffmpeg.exe", filetypes=[("ffmpeg.exe", "ffmpeg.exe"), ("程序", "*.exe")])
        if value:
            self.vars["ffmpeg_path"].set(value)
            self.run_detection()

    def set_status(self, key, text):
        self.after(0, lambda: self.status_vars[key].set(text))

    def run_detection(self):
        self.set_status("项目路径", f"✓ 可搬迁目录：{APP_DIR}")
        self.set_status("Whisper-GPT", "检测中…")
        self.set_status("Python 环境", "等待 Whisper-GPT 路径…")
        self.set_status("FFmpeg / FFprobe", "检测中…")
        self.set_status("GPU / CUDA", "等待 Python 环境…")
        self.set_status("Whisper 模型目录", "等待 Whisper-GPT 路径…")
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self):
        selected = Path(self.vars["whisper_home"].get().strip()).expanduser() if self.vars["whisper_home"].get().strip() else None
        home = selected if selected and (selected / "scripts" / "transcribe.py").is_file() else resolve_whisper_home(load_config())
        if not home:
            self.set_status("Whisper-GPT", "✗ 未找到：请选择包含 scripts\\transcribe.py 的 Whisper-GPT 文件夹")
            return
        self.after(0, lambda: self.vars["whisper_home"].set(str(home)))
        self.set_status("Whisper-GPT", f"✓ {home}")
        python = home / "venv" / "Scripts" / "python.exe"
        if not python.is_file():
            self.set_status("Python 环境", "✗ 未找到 venv\\Scripts\\python.exe")
            self.set_status("GPU / CUDA", "— 需先修复 Python 环境")
        else:
            self.set_status("Python 环境", f"✓ {python}")
            try:
                result = hidden_run([str(python), "-c", "import ctranslate2; print(','.join(ctranslate2.get_supported_compute_types('cuda')))"], capture_output=True, text=True, timeout=20)
                types = result.stdout.strip()
                self.set_status("GPU / CUDA", f"✓ CUDA 可用：{types}" if result.returncode == 0 and types else "○ 未检测到 CUDA，将使用 CPU int8")
            except Exception as exc:
                self.set_status("GPU / CUDA", f"○ GPU 检测失败，将使用 CPU int8：{exc}")
        model_dir = home / "models"
        self.set_status("Whisper 模型目录", f"✓ {model_dir}" if model_dir.exists() else f"○ 尚未下载模型，首次转录会下载到：{model_dir}")
        configured_ffmpeg = self.vars["ffmpeg_path"].get().strip()
        ffmpeg = Path(configured_ffmpeg) if configured_ffmpeg else None
        if not ffmpeg or not ffmpeg.is_file():
            found = shutil.which("ffmpeg")
            ffmpeg = Path(found) if found else None
        if ffmpeg and ffmpeg.is_file():
            probe = ffmpeg.with_name("ffprobe.exe")
            self.after(0, lambda: self.vars["ffmpeg_path"].set(str(ffmpeg)))
            self.set_status("FFmpeg / FFprobe", f"✓ FFmpeg：{ffmpeg}" + ("；FFprobe 可用" if probe.is_file() else "；缺少 ffprobe.exe"))
        else:
            self.set_status("FFmpeg / FFprobe", "✗ 未找到。点击“FFmpeg 下载页”下载后选择 ffmpeg.exe")

    def save_config(self):
        home = self.vars["whisper_home"].get().strip()
        pythonw = Path(home) / "venv" / "Scripts" / "pythonw.exe" if home else None
        data = {
            "whisper_home": home,
            "ffmpeg_path": self.vars["ffmpeg_path"].get().strip(),
            "base_url": self.vars["base_url"].get().strip(),
            "model": self.vars["model"].get().strip(),
            "pythonw_path": str(pythonw) if pythonw and pythonw.is_file() else "",
        }
        try:
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo(TITLE, "配置已保存。请关闭并重新启动视频字幕生成器。")
        except OSError as exc:
            messagebox.showerror(TITLE, f"保存失败：{exc}")

    def test_api(self):
        base_url = self.vars["base_url"].get().strip().rstrip("/")
        key = self.vars["api_key"].get().strip()
        if not base_url:
            messagebox.showerror(TITLE, "请填写 Base URL。")
            return
        self.api_status.set("测试中…")
        def worker():
            url = base_url if base_url.endswith("/models") else (base_url + "/models" if base_url.endswith("/v1") else base_url + "/v1/models")
            request = urllib.request.Request(url, headers={"Authorization": "Bearer " + key} if key else {}, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    data = json.loads(response.read().decode("utf-8"))
                count = len(data.get("data", []))
                self.after(0, lambda: self.api_status.set(f"✓ 可连接，返回 {count} 个模型"))
            except urllib.error.HTTPError as exc:
                self.after(0, lambda: self.api_status.set(f"✗ HTTP {exc.code}：检查 Key、权限或路径"))
            except Exception as exc:
                self.after(0, lambda: self.api_status.set(f"✗ 连接失败：{exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def install_requirements(self):
        home = Path(self.vars["whisper_home"].get().strip())
        python, requirements = home / "venv" / "Scripts" / "python.exe", home / "requirements.txt"
        if not python.is_file() or not requirements.is_file():
            messagebox.showerror(TITLE, "需要先选择有效 Whisper-GPT 文件夹，且其中包含 venv 和 requirements.txt。")
            return
        if not messagebox.askyesno(TITLE, "将执行 pip install -r requirements.txt，可能下载和修改 Whisper-GPT 环境。是否继续？"):
            return
        self.set_status("Python 环境", "正在安装依赖，请等待…")
        def worker():
            try:
                result = hidden_run([str(python), "-m", "pip", "install", "-r", str(requirements)], capture_output=True, text=True, timeout=3600)
                message = "✓ 依赖安装完成" if result.returncode == 0 else "✗ 依赖安装失败：" + result.stderr[-500:]
                self.set_status("Python 环境", message)
            except Exception as exc:
                self.set_status("Python 环境", f"✗ 安装失败：{exc}")
        threading.Thread(target=worker, daemon=True).start()

    def download_model(self):
        home = Path(self.vars["whisper_home"].get().strip())
        python = home / "venv" / "Scripts" / "python.exe"
        model_dir = home / "models"
        if not python.is_file():
            messagebox.showerror(TITLE, "需要先选择有效 Whisper-GPT 文件夹和其 Python 环境。")
            return
        if not messagebox.askyesno(
            TITLE,
            "将下载 Whisper large-v3 模型到 models 文件夹，体积较大并需要联网。是否继续？",
        ):
            return
        self.set_status("Whisper 模型目录", "正在下载 large-v3，请等待…")
        command = [
            str(python), "-c",
            "from faster_whisper import WhisperModel; "
            f"WhisperModel('large-v3', device='cpu', compute_type='int8', download_root={str(model_dir)!r})",
        ]
        def worker():
            try:
                result = hidden_run(command, capture_output=True, text=True, timeout=7200)
                message = "✓ large-v3 模型已就绪" if result.returncode == 0 else "✗ 模型下载失败：" + result.stderr[-500:]
                self.set_status("Whisper 模型目录", message)
            except Exception as exc:
                self.set_status("Whisper 模型目录", f"✗ 下载失败：{exc}")
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    enable_dpi_awareness()
    SetupApp().mainloop()
