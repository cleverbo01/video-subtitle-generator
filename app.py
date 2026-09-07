from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import ctypes
import urllib.error
import urllib.request
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "视频字幕生成器"
APP_DIR = Path(__file__).resolve().parent
PROJECT_CONFIG_FILE = APP_DIR / "project_config.json"


def load_project_config():
    try:
        return json.loads(PROJECT_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def find_whisper_home():
    config = load_project_config()
    candidates = [
        os.environ.get("WHISPER_GPT_HOME"),
        config.get("whisper_home"),
        APP_DIR / "Whisper-GPT",
        APP_DIR.parent / "Whisper-GPT",
        APP_DIR.parent.parent / "Whisper-GPT",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if (path / "scripts" / "transcribe.py").is_file():
            return path.resolve()
    return Path(config.get("whisper_home") or os.environ.get("WHISPER_GPT_HOME") or APP_DIR / "Whisper-GPT")


WHISPER_HOME = find_whisper_home()
TRANSCRIBE_SCRIPT = WHISPER_HOME / "scripts" / "transcribe.py"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WhisperBilingualSubtitleGUI"
CONFIG_FILE = CONFIG_DIR / "settings.json"
MEDIA_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpeg", ".mpg", ".m4v", ".ts",
}

PROFILE_SETTINGS = {
    "普通电影": {
        "vad_filter": True,
        "no_speech_threshold": 0.65,
        "log_prob_threshold": -1.2,
        "suppress_blank": True,
        "initial_prompt": "按影片对白逐字转录，保留人物口语、语气词、重复和未说完的话。",
    },
    "动画片": {
        "vad_filter": True,
        "no_speech_threshold": 0.75,
        "log_prob_threshold": -1.5,
        "suppress_blank": False,
        "initial_prompt": "按动画对白逐字转录，保留角色口癖、拟声词、惊呼、喊叫、重复和语气词。",
    },
    "动作片": {
        "vad_filter": False,
        "no_speech_threshold": 0.82,
        "log_prob_threshold": -1.7,
        "suppress_blank": False,
        "initial_prompt": "按动作影片逐字转录，保留短促对白、低声说话、喊叫、惊呼、喘息和无线电通话。",
    },
    "NSFW": {
        "vad_filter": False,
        "no_speech_threshold": 0.78,
        "log_prob_threshold": -1.5,
        "suppress_blank": True,
        "initial_prompt": "准确记录有实际语义的对白，忽略纯背景声和无意义的非语言声音。",
    },
    "网课": {
        "vad_filter": True,
        "no_speech_threshold": 0.55,
        "log_prob_threshold": -1.0,
        "suppress_blank": True,
        "initial_prompt": "准确转录课程讲解，保留专业术语、数字、公式读法、英文缩写和完整论述。",
    },
    "互联网短视频": {
        "vad_filter": True,
        "no_speech_threshold": 0.72,
        "log_prob_threshold": -1.4,
        "suppress_blank": False,
        "initial_prompt": "按互联网短视频口播逐字转录，保留网络用语、口头禅、语气词、快速对白和中英混说。",
    },
}

SOURCE_LANGUAGE_CODES = {
    "自动检测并整批锁定": None,
    "中文": "zh", "英语": "en", "日语": "ja", "韩语": "ko",
    "西班牙语": "es", "法语": "fr", "德语": "de", "俄语": "ru",
    "葡萄牙语": "pt", "泰语": "th", "越南语": "vi", "阿拉伯语": "ar",
}
TARGET_LANGUAGES = (
    "简体中文", "繁体中文", "英语", "日语", "韩语", "西班牙语",
    "法语", "德语", "俄语", "葡萄牙语", "泰语", "越南语", "阿拉伯语",
)
PERFORMANCE_SETTINGS = {
    "均衡（推荐）": {
        "beam_size": 3,
        "vad_filter": True,
        "vad_parameters": {
            "threshold": 0.25,
            "min_speech_duration_ms": 180,
            "min_silence_duration_ms": 800,
            "speech_pad_ms": 400,
        },
    },
    "精确（较慢）": {
        "beam_size": 5,
        "vad_filter": False,
        "vad_parameters": None,
    },
    "快速": {
        "beam_size": 1,
        "vad_filter": True,
        "vad_parameters": {
            "threshold": 0.35,
            "min_speech_duration_ms": 250,
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 250,
        },
    },
}
VOCALIZATION_PROMPTS = {
    "zh": "嗯，啊，呃，唔，哼，哈，喘息，叹气，吸气，笑声。",
    "en": "Um, uh, ah, mm, hmm, moans, sighs, gasps, breathing, laughter.",
    "ja": "ええ、ああ、うん、ん、はあ、ため息、息遣い、笑い声。",
    "ko": "음, 어, 아, 응, 한숨, 숨소리, 웃음소리.",
    "es": "Eh, ah, mmm, suspiros, jadeos, respiración, risas.",
    "fr": "Euh, ah, hum, soupirs, halètements, respiration, rires.",
    "de": "Ähm, ah, hm, Seufzen, Keuchen, Atmen, Lachen.",
}


@dataclass
class Cue:
    index: int
    timing: str
    text: str


class QueueWriter:
    def __init__(self, messages: queue.Queue):
        self.messages = messages
        self.buffer = ""

    def write(self, value):
        if not value:
            return 0
        self.buffer += str(value)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.messages.put(("log", line))
        return len(value)

    def flush(self):
        if self.buffer:
            self.messages.put(("log", self.buffer))
            self.buffer = ""


class HiddenSubprocessProxy:
    """Keep Whisper-GPT's FFmpeg/FFprobe calls from flashing a console on Windows."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    @staticmethod
    def run(*args, **kwargs):
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            startupinfo = kwargs.get("startupinfo")
            if startupinfo is None:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                kwargs["startupinfo"] = startupinfo
        return subprocess.run(*args, **kwargs)


def enable_windows_dpi_awareness():
    """Opt out of Windows DPI bitmap scaling before Tk creates the first window."""
    if os.name != "nt":
        return
    try:
        # Per-monitor v2 gives the sharpest controls when the window moves
        # between monitors with different scale factors.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class ProfiledWhisperModel:
    """Apply a content-specific decoding profile without changing Whisper-GPT."""

    def __init__(self, model, profile_name, source_language, language_mode, subtitle_content, performance_mode):
        self.model = model
        self.profile_name = profile_name
        self.locked_language = source_language
        self.language_mode = language_mode
        self.subtitle_content = subtitle_content
        self.performance_mode = performance_mode

    def transcribe(self, *args, **kwargs):
        profile = PROFILE_SETTINGS.get(self.profile_name, PROFILE_SETTINGS["普通电影"])
        kwargs.update(profile)
        performance = PERFORMANCE_SETTINGS.get(self.performance_mode, PERFORMANCE_SETTINGS["均衡（推荐）"])
        kwargs.update(performance)
        kwargs["language_detection_segments"] = 5
        kwargs["language_detection_threshold"] = 0.75
        # Word-level alignment prevents Whisper timestamp tokens from letting a
        # short spoken line span an entire silent VAD region.
        kwargs["word_timestamps"] = True
        # The faster-whisper default limits the first timestamp in every
        # decoding window to 1 second.  When music/noise keeps a VAD window
        # open before real dialogue, that limit can pull a later line onto the
        # earlier blank picture.  Allow the full window and suppress likely
        # hallucinations inside long silent stretches instead.
        kwargs["max_initial_timestamp"] = 30.0
        kwargs["hallucination_silence_threshold"] = 2.0
        kwargs["condition_on_previous_text"] = False
        if self.subtitle_content == "完整转录（包含语气声）":
            kwargs["no_speech_threshold"] = max(kwargs.get("no_speech_threshold", 0.6), 0.88)
            kwargs["log_prob_threshold"] = min(kwargs.get("log_prob_threshold", -1.0), -1.8)
            kwargs["suppress_blank"] = False
        # initial_prompt is transcript context, so never inject another language.
        kwargs.pop("initial_prompt", None)
        if self.language_mode == "中外文混合（不锁定语言）":
            kwargs["language"] = None
            kwargs["multilingual"] = True
        elif self.locked_language:
            kwargs["language"] = self.locked_language
            if self.subtitle_content == "完整转录（包含语气声）":
                prompt = VOCALIZATION_PROMPTS.get(self.locked_language)
                if prompt:
                    kwargs["initial_prompt"] = prompt
        else:
            kwargs["language"] = None
        # Keep the hallucination/repetition guard aligned with the effective
        # performance mode.  Performance settings intentionally override the
        # older per-content VAD default above.
        kwargs["compression_ratio_threshold"] = 3.0 if not performance["vad_filter"] else 2.4
        segments, info = self.model.transcribe(*args, **kwargs)
        probability = getattr(info, "language_probability", 0)
        if self.language_mode != "中外文混合（不锁定语言）" and not self.locked_language and getattr(info, "language", None):
            if probability >= 0.75:
                self.locked_language = info.language
                print(f"整批原文语言已锁定：{info.language}（检测置信度 {probability:.2f}）")
            else:
                print(
                    f"语言检测置信度过低：{info.language} / {probability:.2f}，"
                    "本段不锁定，下一分段将重新检测。"
                )
        return segments, info


def analyze_asr_quality(cues, media_duration=None, enforce_coverage=True):
    texts = [re.sub(r"\s+", " ", cue.text).strip() for cue in cues if cue.text.strip()]
    if not texts:
        return False, ["没有识别出有效文字"], {}
    counts = {}
    for text in texts:
        counts[text] = counts.get(text, 0) + 1
    total = len(texts)
    combined = " ".join(texts)
    unique_ratio = len(counts) / total
    top_repeat = max(counts.values())
    repeated_occurrences = sum(count for count in counts.values() if count >= 5)
    replacement_chars = combined.count("\ufffd")
    speech_duration = sum(max(0, cue_end_seconds(cue) - cue_start_seconds(cue)) for cue in cues)
    issues = []
    if replacement_chars:
        issues.append(f"包含 {replacement_chars} 个损坏字符 �")
    if total >= 50 and unique_ratio < 0.35:
        issues.append(f"字幕内容唯一率仅 {unique_ratio:.1%}，存在严重重复/幻觉")
    if total >= 50 and top_repeat / total > 0.08:
        issues.append(f"同一句最多重复 {top_repeat} 次（占 {top_repeat / total:.1%}）")
    if total >= 50 and repeated_occurrences / total > 0.55:
        issues.append(f"高频重复字幕占 {repeated_occurrences / total:.1%}")
    inferred_duration = max((cue_end_seconds(cue) for cue in cues), default=0)
    effective_duration = media_duration or inferred_duration
    if effective_duration >= 600:
        minimum_cues = max(3, int(effective_duration / 600))
        coverage = speech_duration / effective_duration
        coverage_messages = []
        if total < minimum_cues:
            coverage_messages.append(f"{effective_duration / 60:.1f} 分钟媒体仅识别出 {total} 条字幕")
        if coverage < 0.002:
            coverage_messages.append(f"识别时间覆盖率仅 {coverage:.3%}")
        if enforce_coverage:
            issues.extend(message + "，疑似漏识别" for message in coverage_messages)
    else:
        coverage = None
        coverage_messages = []
    metrics = {
        "total": total,
        "unique_ratio": unique_ratio,
        "top_repeat": top_repeat,
        "repeated_ratio": repeated_occurrences / total,
        "replacement_chars": replacement_chars,
        "speech_duration": speech_duration,
        "coverage": coverage,
        "evaluated_duration": effective_duration,
        "coverage_warning": "；".join(coverage_messages),
    }
    return not issues, issues, metrics


LOW_VALUE_VOCALIZATIONS = {
    "嗯", "嗯嗯", "啊", "啊啊", "呃", "呃呃", "唔", "唔唔", "哼", "哼哼", "哦", "喔", "呀",
    "あ", "ああ", "え", "ええ", "う", "うう", "ん", "んん", "はあ", "ふう",
    "아", "아아", "어", "어어", "으", "으으", "음", "음음", "흠", "하", "후", "흐",
    "ah", "aah", "uh", "um", "umm", "mm", "mmm", "hmm", "oh", "ooh", "ugh",
}


def normalized_vocalization(text):
    value = text.lower().replace("\ufffd", "")
    value = re.sub(r"[\s.,!?，。！？、~～…·'\"“”‘’\-]+", "", value)
    return value


def is_low_value_vocalization(text):
    value = normalized_vocalization(text)
    if not value:
        return True
    if value in LOW_VALUE_VOCALIZATIONS:
        return True
    # Repeated single vocalization characters such as 啊啊啊 / 아아아 / mmmm.
    if len(value) <= 12 and len(set(value)) == 1:
        return value[0] in "嗯啊呃唔哼哦喔呀あえうんはふ아어으음흠하후흐ahum"
    return False


def clean_dialogue_cues(cues):
    cleaned = []
    removed = 0
    merged = 0
    for cue in cues:
        text = cue.text.replace("\ufffd", "").strip()
        if is_low_value_vocalization(text):
            removed += 1
            continue
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if cleaned:
            previous = cleaned[-1]
            previous_normalized = re.sub(r"\s+", " ", previous.text).strip().lower()
            gap = cue_start_seconds(cue) - cue_end_seconds(previous)
            if normalized == previous_normalized and gap <= 0.5:
                end_text = cue.timing.split(" --> ", 1)[1]
                previous.timing = previous.timing.split(" --> ", 1)[0] + " --> " + end_text
                merged += 1
                continue
        cleaned.append(Cue(len(cleaned) + 1, cue.timing, text))
    return cleaned, {"input": len(cues), "output": len(cleaned), "removed": removed, "merged": merged}


def load_transcribe_module():
    if not TRANSCRIBE_SCRIPT.is_file():
        raise FileNotFoundError(f"找不到 Whisper 脚本：{TRANSCRIBE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("whisper_gpt_transcribe", TRANSCRIBE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 Whisper 转录脚本")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # The script imports the stdlib subprocess module directly.  Rebind only
    # its module-level reference, leaving the rest of this app untouched.
    module.subprocess = HiddenSubprocessProxy()
    return module


def parse_srt(path: Path) -> list[Cue]:
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    cues = []
    for block in re.split(r"\n{2,}", content):
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            index = len(cues) + 1
        cues.append(Cue(index, lines[1].strip(), "\n".join(lines[2:]).strip()))
    if not cues:
        raise RuntimeError(f"字幕为空或格式无法识别：{path.name}")
    return cues


def api_endpoint_urls(base_url: str, protocol: str) -> list[str]:
    url = base_url.strip().rstrip("/")
    if not url:
        raise ValueError("API Base URL 不能为空")
    endpoint = "responses" if protocol == "responses" else "chat/completions"
    if url.endswith("/responses") or url.endswith("/chat/completions"):
        return [url]
    if url.endswith("/v1"):
        return [url + "/" + endpoint]
    return [url + "/v1/" + endpoint, url + "/" + endpoint]


def chat_completions_url(base_url: str) -> str:
    return api_endpoint_urls(base_url, "chat")[0]


class ApiRequestError(RuntimeError):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class TranslationIncompleteError(RuntimeError):
    """Some cue translations are still missing, while completed cues are cached."""


def friendly_api_error(errors, model):
    combined = "\n".join(item[2] for item in errors).lower()
    codes = {item[1] for item in errors if item[1] is not None}
    if "upstream access forbidden" in combined:
        reason = "中转站地址和 Key 已连接成功，但中转站配置的上游账户、渠道或内容策略拒绝了本次请求。"
        action = "这不是本地程序或接口路径错误。请在中转站更换支持该模型和内容的渠道/分组，改用其他模型或服务商，或联系中转站管理员。"
    elif "upstream service temporarily unavailable" in combined:
        reason = "中转站已收到请求，但它连接的上游服务暂时不可用。"
        action = "请稍后重试，或在中转站切换上游渠道/模型。"
    elif 401 in codes:
        reason = "API Key 无效、已过期，或 Authorization 格式不被该中转站接受。"
        action = "请重新复制 API Key；若中转站有渠道分组或令牌权限，请确认已授权。"
    elif 403 in codes:
        reason = "服务器拒绝访问，常见原因是 Key 权限、IP/WAF 限制或账户无权使用该接口。"
        action = "请检查中转站账户权限、白名单和余额，或联系中转站管理员。"
    elif 429 in codes:
        reason = "请求频率、额度或余额受限。"
        action = "请稍后重试，并检查中转站余额、并发限制和模型额度。"
    elif "not supported" in combined or "unsupported" in combined or "model_not_found" in combined:
        reason = f"连接地址和 Key 已到达服务器，但当前账户分组不支持模型“{model}”。"
        action = "请点击“获取模型”并选择中转站实际返回的模型；也可检查中转站的模型映射/分组配置。"
    elif 405 in codes:
        reason = "服务器存在，但当前请求路径不允许 POST，通常是接口格式或端点路径不匹配。"
        action = "程序已自动尝试 Responses 和 Chat；若仍失败，请从中转站复制完整 API 端点。"
    elif 404 in codes:
        reason = "接口路径或模型不存在。程序已尝试常见的 /v1 路径和两种 OpenAI 协议。"
        action = "请核对中转站给出的 Base URL，并点击“获取模型”确认模型名称。"
    else:
        reason = "服务器返回了无法自动归类的错误。"
        action = "请检查 Base URL、接口格式、模型、Key 和中转站服务状态。"
    attempts = []
    for url, code, detail in errors:
        clean = re.sub(r"\s+", " ", detail).strip()[:300]
        attempts.append(f"- {url} -> {('HTTP ' + str(code)) if code else '网络错误'}: {clean}")
    return f"原因：{reason}\n\n建议：{action}\n\n已自动尝试：\n" + "\n".join(attempts)


def post_openai_request(base_url, headers, chat_payload, responses_payload, api_format, timeout):
    errors = []
    protocols = {
        "自动（优先 Responses）": ("responses", "chat"),
        "Responses API": ("responses",),
        "Chat Completions": ("chat",),
    }.get(api_format, ("responses", "chat"))
    normalized_base = base_url.strip().rstrip("/")
    if normalized_base.endswith("/responses"):
        protocols = ("responses",)
    elif normalized_base.endswith("/chat/completions"):
        protocols = ("chat",)
    for protocol in protocols:
        payload = responses_payload if protocol == "responses" else chat_payload
        for url in api_endpoint_urls(base_url, protocol):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8")), url, protocol
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                errors.append((url, exc.code, detail))
                detail_lower = detail.lower()
                if "upstream access forbidden" in detail_lower:
                    raise ApiRequestError(
                        friendly_api_error(errors, chat_payload.get("model", "")),
                        retryable=False,
                    ) from exc
                if exc.code == 502:
                    raise ApiRequestError(
                        friendly_api_error(errors, chat_payload.get("model", "")),
                        retryable=True,
                    ) from exc
                if exc.code in {401, 403, 429}:
                    raise ApiRequestError(
                        friendly_api_error(errors, chat_payload.get("model", "")),
                        retryable=exc.code == 429,
                    ) from exc
            except urllib.error.URLError as exc:
                errors.append((url, None, str(exc.reason)))
    retryable = any(
        code is None or code in {408, 425, 429, 500, 502, 503, 504}
        for _url, code, _detail in errors
    )
    raise ApiRequestError(
        friendly_api_error(errors, chat_payload.get("model", "")),
        retryable=retryable,
    )


def models_endpoint_urls(base_url):
    url = base_url.strip().rstrip("/")
    url = re.sub(r"/(?:chat/completions|responses)$", "", url)
    if url.endswith("/v1"):
        return [url + "/models"]
    return [url + "/v1/models", url + "/models"]


def fetch_models(base_url, api_key, timeout=30):
    headers = {}
    if api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()
    errors = []
    for url in models_endpoint_urls(base_url):
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            models = sorted({str(item["id"]) for item in result.get("data", []) if item.get("id")})
            if models:
                return models, url
            errors.append(f"{url} 返回成功，但没有模型列表")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("无法获取模型列表。中转站可能未开放 /models。\n" + "\n".join(errors))


def extract_response_text(result, protocol):
    if protocol == "chat":
        content = result["choices"][0]["message"]["content"]
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content)
    if isinstance(result.get("output_text"), str):
        return result["output_text"]
    parts = []
    for output in result.get("output", []):
        for content in output.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    if not parts:
        raise ValueError("Responses API 响应中没有可读取的 output_text")
    return "".join(parts)


def test_chat_api(base_url, api_key, model, api_format, timeout=30):
    chat_payload = {
        "model": model.strip(),
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    }
    responses_payload = {
        "model": model.strip(),
        "input": "Reply with exactly: OK",
    }
    if not chat_payload["model"]:
        raise ValueError("模型名称不能为空")
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()
    result, used_url, protocol = post_openai_request(
        base_url, headers, chat_payload, responses_payload, api_format, timeout
    )
    return extract_response_text(result, protocol).strip(), used_url, protocol


def extract_json_array(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        preview = re.sub(r"\s+", " ", text).strip()[:300]
        raise ValueError(f"模型响应中没有 JSON 数组；响应开头：{preview}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        preview = re.sub(r"\s+", " ", text).strip()[:300]
        raise ValueError(f"模型返回的 JSON 不完整或格式错误；响应开头：{preview}") from exc


def translate_batch(base_url, api_key, model, api_format, target_language, texts, media_name="", timeout=120):
    items = [{"id": i + 1, "text": text} for i, text in enumerate(texts)]
    system = (
        f"你是专业影视字幕翻译器。当前媒体文件名是《{media_name}》，目标语言是{target_language}。\n"
        "输入是按时间顺序排列的完整字幕中的一个连续批次，每项 id 对应一个时间轴字幕块。\n"
        "翻译规则：\n"
        "1. 必须逐项一一对应，保留全部 id；禁止遗漏、合并、拆分、改变顺序。\n"
        f"2. translation 必须是自然、简洁、符合上下文的{target_language}，不得附带原文或解释。\n"
        "3. 人名、称呼、术语和代词在整个批次中保持一致；结合前后字幕理解省略主语。\n"
        "4. 忠实保留原文的语气强度、重复、口吃、口语和非词汇发声，不删减、不弱化、不额外发挥。\n"
        "5. 不要输出时间轴，不要使用 Markdown，不要添加审查、警告或说明。\n"
        "6. 只返回严格 JSON 数组，格式为 [{\"id\":1,\"translation\":\"译文\"}]。"
    )
    chat_payload = {
        "model": model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ],
    }
    responses_payload = {
        "model": model.strip(),
        "instructions": system,
        "input": json.dumps(items, ensure_ascii=False),
    }
    if not chat_payload["model"]:
        raise ValueError("翻译模型名称不能为空")
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()
    result, _used_url, protocol = post_openai_request(
        base_url, headers, chat_payload, responses_payload, api_format, timeout
    )
    content = extract_response_text(result, protocol)
    translated = extract_json_array(content)
    mapping = {int(item["id"]): str(item["translation"]).strip() for item in translated}
    if len(mapping) != len(texts) or any(i not in mapping for i in range(1, len(texts) + 1)):
        raise ValueError(
            f"翻译结果条目数量或 id 不匹配；期望 {len(texts)} 条，"
            f"实际 {len(mapping)} 条，收到 id={sorted(mapping)[:30]}"
        )
    return [mapping[i] for i in range(1, len(texts) + 1)]


def cue_start_seconds(cue: Cue):
    match = re.match(r"(\d+):(\d+):(\d+),(\d+)", cue.timing)
    if not match:
        return 0.0
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def cue_end_seconds(cue: Cue):
    end_text = cue.timing.split(" --> ", 1)[1]
    return cue_start_seconds(Cue(0, end_text + " --> " + end_text, ""))


def format_srt_seconds(seconds):
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1 for char in text)


def expected_cue_duration(text):
    """A conservative on-screen duration ceiling for a single subtitle cue."""
    width = max(1, display_width(re.sub(r"\s+", " ", text).strip()))
    # About five display-width units per second, with enough time for short
    # Japanese/Chinese lines.  A single cue must never occupy minutes of video.
    return min(12.0, max(3.0, 1.8 + width / 5.0))


def repair_cue_timing(cues):
    """Clamp broken ASR segment ends while retaining their detected start time."""
    repaired = []
    clamped = 0
    for index, cue in enumerate(cues):
        start = cue_start_seconds(cue)
        end = cue_end_seconds(cue)
        limit = expected_cue_duration(cue.text)
        if end - start > limit:
            end = start + limit
            clamped += 1
        # Do not let a cue cover the beginning of the next detected utterance.
        if index + 1 < len(cues):
            next_start = cue_start_seconds(cues[index + 1])
            if next_start > start:
                end = min(end, max(start + 0.25, next_start - 0.03))
        end = max(start + 0.05, end)
        repaired.append(Cue(len(repaired) + 1, f"{format_srt_seconds(start)} --> {format_srt_seconds(end)}", cue.text))
    return repaired, {"input": len(cues), "clamped": clamped, "output": len(repaired)}


def split_text_balanced(text, parts):
    text = re.sub(r"\s+", " ", text).strip()
    if parts <= 1 or not text:
        return [text]
    total = max(1, display_width(text))
    targets = [total * number / parts for number in range(1, parts)]
    chunks, current, width, target_index = [], [], 0, 0
    for char in text:
        current.append(char)
        width += display_width(char)
        if target_index < len(targets) and width >= targets[target_index]:
            chunks.append("".join(current).strip())
            current = []
            target_index += 1
    chunks.append("".join(current).strip())
    while len(chunks) < parts:
        chunks.append("")
    return chunks[:parts]


def optimize_bilingual_timing(cues, translations, max_line_width=64, min_duration=1.2):
    """Prevent PotPlayer from clipping wrapped first lines and flashing tiny cues."""
    optimized_cues, optimized_translations = [], []
    index = 0
    while index < len(cues):
        cue = cues[index]
        translation = translations[index]
        start, end = cue_start_seconds(cue), cue_end_seconds(cue)
        source_text, target_text = cue.text, translation

        # Very short adjacent cues flicker in SRT renderers; combine them before splitting.
        while end - start < min_duration and index + 1 < len(cues):
            next_cue = cues[index + 1]
            next_start = cue_start_seconds(next_cue)
            if next_start - end > 0.35:
                end = min(next_start, start + min_duration)
                break
            index += 1
            source_text = (source_text + " " + next_cue.text).strip()
            target_text = (target_text + " " + translations[index]).strip()
            end = cue_end_seconds(next_cue)

        if end - start < min_duration:
            next_start = cue_start_seconds(cues[index + 1]) if index + 1 < len(cues) else start + min_duration
            end = max(end, min(start + min_duration, next_start))

        parts = max(
            1,
            math.ceil(display_width(source_text) / max_line_width),
            math.ceil(display_width(target_text) / max_line_width),
        )
        source_parts = split_text_balanced(source_text, parts)
        target_parts = split_text_balanced(target_text, parts)
        duration = max(0.001, end - start)
        for part in range(parts):
            part_start = start + duration * part / parts
            part_end = start + duration * (part + 1) / parts
            timing = f"{format_srt_seconds(part_start)} --> {format_srt_seconds(part_end)}"
            optimized_cues.append(Cue(len(optimized_cues) + 1, timing, source_parts[part]))
            optimized_translations.append(target_parts[part])
        index += 1
    return optimized_cues, optimized_translations


def split_translation_batches(cues, chunk_minutes=60, max_chars=8000, max_items=150):
    batches, current = [], []
    batch_start, char_count = None, 0
    max_seconds = max(1, chunk_minutes) * 60
    for index, cue in enumerate(cues):
        start = cue_start_seconds(cue)
        text_size = len(cue.text)
        if current and (
            (start - batch_start) >= max_seconds
            or char_count + text_size > max_chars
            or len(current) >= max_items
        ):
            batches.append(current)
            current, batch_start, char_count = [], None, 0
        if batch_start is None:
            batch_start = start
        current.append((index, cue))
        char_count += text_size
    if current:
        batches.append(current)
    return batches


def translation_cache_signature(cues):
    digest = hashlib.sha256()
    for cue in cues:
        digest.update(cue.timing.encode("utf-8"))
        digest.update(b"\0")
        digest.update(cue.text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def load_translation_cache(path, cues, target_language, model, log):
    translations = [None] * len(cues)
    if not path or not path.exists():
        return translations
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("version") != 1
            or data.get("source_signature") != translation_cache_signature(cues)
            or data.get("target_language") != target_language
            or data.get("model") != model
        ):
            log(f"[翻译缓存] 字幕内容、目标语言或模型已变化，本次不复用旧缓存：{path.name}")
            return translations
        for raw_index, value in data.get("translations", {}).items():
            index = int(raw_index)
            if 0 <= index < len(translations) and isinstance(value, str) and value.strip():
                translations[index] = value
        restored = sum(value is not None for value in translations)
        if restored:
            log(f"[翻译缓存] 已恢复 {restored}/{len(cues)} 条，仅补译缺失内容。")
    except Exception as exc:
        log(f"[翻译缓存] 缓存无法读取，将重新翻译：{exc}")
    return translations


def save_translation_cache(path, cues, translations, target_language, model):
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "source_signature": translation_cache_signature(cues),
        "target_language": target_language,
        "model": model,
        "translations": {
            str(index): value for index, value in enumerate(translations) if value is not None
        },
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def translate_cues_parallel(
    cues, base_url, api_key, model, api_format, target_language,
    chunk_minutes, concurrency, api_timeout, cancelled, log, progress_callback, media_name="",
    cache_path=None,
):
    batches = split_translation_batches(cues, chunk_minutes)
    translations = load_translation_cache(cache_path, cues, target_language, model, log)
    cache_lock = threading.Lock()
    fallback_indices = []

    def store_results(piece, values):
        with cache_lock:
            for (index, _cue), value in zip(piece, values):
                translations[index] = value
            save_translation_cache(cache_path, cues, translations, target_language, model)

    def process_batch(batch_number, batch):
        def request_piece(piece, label, depth=0):
            if cancelled.is_set():
                raise InterruptedError("用户已请求停止")

            def fallback_to_source(reason):
                values = [cue.text for _index, cue in piece]
                indices = [index + 1 for index, _cue in piece]
                store_results(piece, values)
                with cache_lock:
                    fallback_indices.extend(indices)
                index_text = str(indices[0]) if len(indices) == 1 else f"{indices[0]}-{indices[-1]}"
                log(
                    f"[自动降级] {media_name} 第 {index_text} 条字幕翻译失败，"
                    f"已使用原文回填，任务继续。原因：{reason}"
                )
                return piece, values

            last_error = None
            max_attempts = 1 if len(piece) <= 10 else 3
            for attempt in range(1, max_attempts + 1):
                try:
                    # A tiny fallback piece should not inherit a 5–15 minute timeout.
                    # Large batches still honor the user setting, while isolated cues
                    # fail fast enough to let the remaining translation continue.
                    piece_timeout = min(api_timeout, 45 if len(piece) == 1 else max(60, 60 + len(piece) * 2))
                    values = translate_batch(
                        base_url, api_key, model, api_format, target_language,
                        [cue.text for _, cue in piece],
                        media_name=f"{media_name}（{label}）",
                        timeout=piece_timeout,
                    )
                    store_results(piece, values)
                    return piece, values
                except Exception as exc:
                    last_error = exc
                    if isinstance(exc, ApiRequestError) and not exc.retryable:
                        raise RuntimeError(f"{media_name} {label} 不可重试：{exc}") from exc
                    error_text = str(exc).lower()
                    is_timeout = "timed out" in error_text or "timeout" in error_text
                    is_disconnect = any(
                        marker in error_text
                        for marker in (
                            "remote end closed connection", "connection reset",
                            "connection aborted", "incompleteread", "eof occurred",
                            "server disconnected", "temporarily unavailable",
                        )
                    )
                    is_format_error = any(
                        marker in error_text
                        for marker in (
                            "没有 json 数组", "json 不完整", "jsondecodeerror",
                            "条目数量或 id 不匹配", "expecting value", "unterminated string",
                        )
                    )
                    if (is_timeout or is_disconnect) and len(piece) <= 10:
                        return fallback_to_source(exc)
                    can_split = len(piece) > 1
                    if (is_timeout or is_disconnect or is_format_error) and can_split:
                        midpoint = len(piece) // 2
                        reason = (
                            "请求超时" if is_timeout
                            else "连接被中转站断开" if is_disconnect
                            else "模型 JSON 输出不完整"
                        )
                        log(
                            f"{media_name} {label} {reason}，自动拆分为 "
                            f"{len(piece[:midpoint])} + {len(piece[midpoint:])} 条继续翻译"
                        )
                        left_piece, left_values = request_piece(piece[:midpoint], label + "A", depth + 1)
                        right_piece, right_values = request_piece(piece[midpoint:], label + "B", depth + 1)
                        return left_piece + right_piece, left_values + right_values
                    if attempt < max_attempts:
                        log(f"{media_name} {label} 失败，第 {attempt}/{max_attempts} 次重试：{exc}")
                        time.sleep(attempt * 2)
            if len(piece) == 1:
                return fallback_to_source(last_error)
            raise RuntimeError(f"{media_name} {label} 连续失败：{last_error}")

        return request_piece(batch, f"第 {batch_number}/{len(batches)} 段")

    with ThreadPoolExecutor(max_workers=max(1, min(5, concurrency))) as executor:
        pending_batches = [
            [(index, cue) for index, cue in batch if translations[index] is None]
            for batch in batches
        ]
        futures = {
            executor.submit(process_batch, number, batch): number
            for number, batch in enumerate(pending_batches, 1) if batch
        }
        completed_batches = len(batches) - len(futures)
        for _ in range(completed_batches):
            progress_callback()
        for future in as_completed(futures):
            if cancelled.is_set():
                raise InterruptedError("用户已请求停止")
            batch, values = future.result()
            completed_batches += 1
            log(
                f"{media_name} 翻译进度：{completed_batches}/{len(batches)} "
                f"（完成原始分段 {futures[future]}）"
            )
            progress_callback()
    missing = [index + 1 for index, value in enumerate(translations) if value is None]
    if missing:
        preview = ", ".join(map(str, missing[:30]))
        suffix = "…" if len(missing) > 30 else ""
        completed = len(translations) - len(missing)
        raise TranslationIncompleteError(
            f"{media_name} 已缓存 {completed}/{len(translations)} 条，仍有 {len(missing)} 条未完成"
            f"（序号：{preview}{suffix}）。\n"
            "请稍后直接重新翻译，程序只会补译这些条目。"
        )
    if fallback_indices:
        preview = ", ".join(map(str, sorted(fallback_indices)[:50]))
        suffix = "…" if len(fallback_indices) > 50 else ""
        log(
            f"[翻译降级汇总] 共 {len(fallback_indices)} 条因 API 持续失败使用原文回填；"
            f"字幕序号：{preview}{suffix}"
        )
    return translations


def write_bilingual_srt(path: Path, cues: list[Cue], translations: list[str]):
    cues, _timing_stats = repair_cue_timing(cues)
    cues, translations = optimize_bilingual_timing(cues, translations)
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="\n") as stream:
        for number, (cue, chinese) in enumerate(zip(cues, translations), 1):
            stream.write(f"{number}\n{cue.timing}\n{cue.text}\n{chinese}\n\n")
    temp.replace(path)


def write_single_language_srt(path: Path, cues: list[Cue], texts: list[str] | None = None):
    values = texts if texts is not None else [cue.text for cue in cues]
    cues, _timing_stats = repair_cue_timing(cues)
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="\n") as stream:
        for number, (cue, text) in enumerate(zip(cues, values), 1):
            stream.write(f"{number}\n{cue.timing}\n{text}\n\n")
    temp.replace(path)


def translated_suffix(target_language):
    if target_language == "简体中文":
        return "中文版"
    if target_language == "繁体中文":
        return "繁体中文版"
    return target_language + "版"


def copy_to_bundle(source: Path, destination: Path):
    """Copy an artifact without replacing an unrelated same-named source file."""
    if not source.is_file():
        return "missing"
    try:
        if source.resolve() == destination.resolve():
            return "same"
    except OSError:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        source_stat, destination_stat = source.stat(), destination.stat()
        if (
            source_stat.st_size == destination_stat.st_size
            and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
        ):
            return "up_to_date"
    shutil.copy2(source, destination)
    return "copied"


def media_output_dir(source: Path, input_dir: Path, output_dir: Path, bundle_output: bool):
    base_dir = output_dir / source.parent.relative_to(input_dir)
    return base_dir / source.stem if bundle_output else base_dir


def migrate_legacy_outputs(source: Path, legacy_dir: Path, target_dir: Path, target_language: str, log):
    """Move only known program artifacts into the new bundle layout."""
    suffix = translated_suffix(target_language)
    names = (
        f"{source.stem}_原声版.srt",
        f"{source.stem}_字幕.srt",
        f"{source.stem}_中外版.srt",
        f"{source.stem}_{suffix}.srt",
        f"{source.stem}_转录文本.txt",
        f"{source.stem}_raw.txt",
        f"{source.stem}_质量不合格.srt",
        f"{source.stem}_原声版_时间轴已修复.srt",
        f"{source.stem}_中外版_时间轴已修复.srt",
        f"{source.stem}_{suffix}_时间轴已修复.srt",
    )
    moved = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        old_path, new_path = legacy_dir / name, target_dir / name
        if old_path.is_file() and not new_path.exists():
            shutil.move(str(old_path), str(new_path))
            moved.append(name)
    old_cache = legacy_dir / ".translation_cache" / f"{source.stem}_{suffix}.json"
    new_cache = target_dir / ".translation_cache" / old_cache.name
    if old_cache.is_file() and not new_cache.exists():
        new_cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_cache), str(new_cache))
        moved.append(".translation_cache/" + old_cache.name)
    if moved:
        log(f"[同名文件夹迁移] {source.name}：已移动 {len(moved)} 个已有输出到 {target_dir}")


def bundle_media_outputs(source: Path, target_dir: Path, settings, log):
    """Put only the original media beside artifacts already written to the bundle."""
    copied = copy_to_bundle(source, target_dir / source.name)
    required = []
    if settings["output_original"]:
        required.append(f"{source.stem}_原声版.srt")
    if settings["output_bilingual"]:
        required.append(f"{source.stem}_中外版.srt")
    if settings["output_translated"]:
        required.append(f"{source.stem}_{translated_suffix(settings['target_language'])}.srt")
    required.append(f"{source.stem}_转录文本.txt")
    missing = [name for name in required if not (target_dir / name).is_file()]
    log(
        f"[同名文件夹整理] {source.name} -> {target_dir}；"
        f"视频{'已复制' if copied == 'copied' else '已存在'}，字幕和文本直接写入此文件夹。"
    )
    if missing:
        log("[同名文件夹整理] 未找到：" + "、".join(missing))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        if os.name == "nt":
            try:
                self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
            except tk.TclError:
                pass
        self.title(APP_TITLE)
        self.geometry("960x780")
        self.minsize(820, 680)
        self.messages = queue.Queue()
        self.cancelled = threading.Event()
        self.worker = None
        self.vars = {
            "input": tk.StringVar(),
            "output": tk.StringVar(),
            "recursive": tk.BooleanVar(value=True),
            "overwrite": tk.BooleanVar(value=False),
            "profile": tk.StringVar(value="普通电影"),
            "subtitle_content": tk.StringVar(value="完整转录（包含语气声）"),
            "performance_mode": tk.StringVar(value="均衡（推荐）"),
            "language_mode": tk.StringVar(value="纯单语言（整批锁定）"),
            "language": tk.StringVar(value="自动检测并整批锁定"),
            "target_language": tk.StringVar(value="简体中文"),
            "output_original": tk.BooleanVar(value=True),
            "output_bilingual": tk.BooleanVar(value=True),
            "output_translated": tk.BooleanVar(value=True),
            "bundle_output": tk.BooleanVar(value=False),
            "retranscribe_existing": tk.BooleanVar(value=False),
            "allow_low_quality": tk.BooleanVar(value=False),
            "device": tk.StringVar(value="GPU"),
            "base_url": tk.StringVar(value=str(load_project_config().get("base_url", ""))),
            "api_key": tk.StringVar(),
            "remember_key": tk.BooleanVar(value=False),
            "model": tk.StringVar(value=str(load_project_config().get("model", ""))),
            "api_format": tk.StringVar(value="自动（优先 Responses）"),
            "chunk_minutes": tk.IntVar(value=60),
            "concurrency": tk.IntVar(value=3),
            "api_timeout": tk.IntVar(value=300),
        }
        self.load_settings()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll_messages)
        self.after(450, self.offer_environment_setup)
        threading.Thread(target=self.detect_gpu, daemon=True).start()

    def build_ui(self):
        self.configure(bg="#F4F7FB")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#F4F7FB")
        style.configure("TLabel", background="#F4F7FB", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"), foreground="#163B65")
        style.configure("Subtitle.TLabel", foreground="#64748B")
        style.configure("Card.TLabelframe", background="#FFFFFF", bordercolor="#D8E1ED", relief="solid")
        style.configure("Card.TLabelframe.Label", background="#FFFFFF", foreground="#163B65", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), foreground="#FFFFFF", background="#2563EB", bordercolor="#2563EB", padding=(14, 7))
        style.map("Accent.TButton", background=[("active", "#1D4ED8")])
        style.configure("TButton", padding=(9, 5))
        style.configure("TCheckbutton", background="#FFFFFF")
        style.configure("TCombobox", padding=3)
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(9, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="本地转录 · API 翻译 · PotPlayer 字幕", style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="环境配置助手", command=self.open_environment_setup).grid(row=0, column=1, rowspan=2, sticky="e")
        self.path_row(root, 1, "媒体文件夹", "input", self.pick_input)
        self.path_row(root, 2, "输出文件夹", "output", self.pick_output)

        options = ttk.LabelFrame(root, text="识别设置", style="Card.TLabelframe", padding=10)
        options.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Checkbutton(options, text="扫描子文件夹（输出时保留目录结构）", variable=self.vars["recursive"]).grid(row=0, column=0, padx=(0, 20))
        ttk.Label(options, text="语言模式").grid(row=0, column=1)
        ttk.Combobox(options, textvariable=self.vars["language_mode"], width=22, values=("纯单语言（整批锁定）", "中外文混合（不锁定语言）"), state="readonly").grid(row=0, column=2, padx=6)
        ttk.Label(options, text="设备").grid(row=0, column=3, padx=(16, 0))
        ttk.Combobox(options, textvariable=self.vars["device"], width=10, values=("auto", "GPU", "CPU"), state="readonly").grid(row=0, column=4, padx=6)
        ttk.Checkbutton(options, text="覆盖已有输出", variable=self.vars["overwrite"]).grid(row=0, column=5, padx=(16, 0))
        ttk.Label(options, text="内容预设").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            options,
            textvariable=self.vars["profile"],
            values=tuple(PROFILE_SETTINGS),
            state="readonly",
            width=22,
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(options, text="原文语言").grid(row=1, column=3, sticky="e", pady=(8, 0))
        ttk.Combobox(options, textvariable=self.vars["language"], width=20, values=tuple(SOURCE_LANGUAGE_CODES), state="readonly").grid(row=1, column=4, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(options, text="第二行语言").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            options,
            textvariable=self.vars["target_language"],
            values=TARGET_LANGUAGES,
            state="readonly",
            width=14,
        ).grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(options, text="字幕内容").grid(row=2, column=3, sticky="e", pady=(8, 0))
        ttk.Combobox(
            options,
            textvariable=self.vars["subtitle_content"],
            values=("对白优先（过滤拟声词）", "完整转录（包含语气声）"),
            state="readonly",
            width=22,
        ).grid(row=2, column=4, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self.gpu_status = ttk.Label(options, text="GPU：检测中…")
        ttk.Label(options, text="性能模式").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            options,
            textvariable=self.vars["performance_mode"],
            values=tuple(PERFORMANCE_SETTINGS),
            state="readonly",
            width=18,
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self.gpu_status.grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 0))

        outputs = ttk.LabelFrame(root, text="需要生成的字幕版本（至少勾选一个）", style="Card.TLabelframe", padding=8)
        outputs.grid(row=4, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Checkbutton(outputs, text="原声版（只有原文）", variable=self.vars["output_original"]).grid(row=0, column=0, sticky="w", padx=(0, 24))
        ttk.Checkbutton(outputs, text="中外版（原文 + 译文）", variable=self.vars["output_bilingual"]).grid(row=0, column=1, sticky="w", padx=(0, 24))
        ttk.Checkbutton(outputs, text="目标语言版（只有译文）", variable=self.vars["output_translated"]).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(outputs, text="重新转录已有原声版", variable=self.vars["retranscribe_existing"]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(outputs, text="低质量也强制翻译（不推荐）", variable=self.vars["allow_low_quality"]).grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            outputs,
            text="完成后复制视频、转录文本和已勾选字幕到同名文件夹（不移动原文件）",
            variable=self.vars["bundle_output"],
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        api = ttk.LabelFrame(root, text="OpenAI-compatible 翻译 API", style="Card.TLabelframe", padding=10)
        api.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        api.columnconfigure(1, weight=1)
        ttk.Label(api, text="Base URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.vars["base_url"]).grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(api, text="API Key").grid(row=1, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.vars["api_key"], show="●").grid(row=1, column=1, sticky="ew", padx=8, pady=3)
        ttk.Checkbutton(api, text="保存 Key（本机明文）", variable=self.vars["remember_key"]).grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Label(api, text="模型").grid(row=2, column=0, sticky="w")
        self.model_box = ttk.Combobox(api, textvariable=self.vars["model"], state="normal")
        self.model_box.grid(row=2, column=1, sticky="ew", padx=8, pady=3)
        self.models_button = ttk.Button(api, text="获取模型", command=self.load_models)
        self.models_button.grid(row=2, column=2, padx=4)
        ttk.Label(api, text="每段分钟").grid(row=2, column=3, sticky="e")
        ttk.Spinbox(api, from_=5, to=120, textvariable=self.vars["chunk_minutes"], width=6).grid(row=2, column=4, padx=8)
        self.api_test_button = ttk.Button(api, text="测试 API", command=self.test_api)
        self.api_test_button.grid(row=0, column=2, columnspan=2, padx=8)
        ttk.Label(api, text="接口格式").grid(row=3, column=0, sticky="w")
        ttk.Combobox(
            api,
            textvariable=self.vars["api_format"],
            values=("自动（优先 Responses）", "Responses API", "Chat Completions"),
            state="readonly",
            width=24,
        ).grid(row=3, column=1, sticky="w", padx=8, pady=3)
        ttk.Label(api, text="并发数（1–5）").grid(row=3, column=3, sticky="e")
        ttk.Spinbox(api, from_=1, to=5, textvariable=self.vars["concurrency"], width=6).grid(row=3, column=4, padx=8)
        ttk.Label(api, text="超时秒数").grid(row=4, column=3, sticky="e")
        ttk.Spinbox(api, from_=60, to=900, increment=30, textvariable=self.vars["api_timeout"], width=6).grid(row=4, column=4, padx=8, pady=3)

        buttons = ttk.Frame(root)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        self.start_button = ttk.Button(buttons, text="开始生成字幕", command=self.start, style="Accent.TButton")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="打开输出文件夹", command=self.open_output).pack(side="right")

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(2, 6))
        self.status = ttk.Label(root, text="就绪", style="Subtitle.TLabel")
        self.status.grid(row=8, column=0, columnspan=3, sticky="w")
        self.log_box = tk.Text(root, height=18, wrap="word", state="disabled", font=("Consolas", 10), bg="#FFFFFF", relief="solid", borderwidth=1, highlightthickness=0)
        self.log_box.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        scroll = ttk.Scrollbar(root, command=self.log_box.yview)
        scroll.grid(row=9, column=3, sticky="ns", pady=(6, 0))
        self.log_box.configure(yscrollcommand=scroll.set)

    def path_row(self, parent, row, label, key, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="选择…", command=command).grid(row=row, column=2)

    def pick_input(self):
        value = filedialog.askdirectory(title="选择媒体文件夹")
        if value:
            self.vars["input"].set(value)
            if not self.vars["output"].get():
                self.vars["output"].set(str(Path(value) / "双语字幕"))

    def pick_output(self):
        value = filedialog.askdirectory(title="选择输出文件夹")
        if value:
            self.vars["output"].set(value)

    def load_settings(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        for key in (
            "input", "output", "recursive", "overwrite", "profile", "subtitle_content", "performance_mode", "language_mode", "language",
            "target_language", "output_original", "output_bilingual", "output_translated", "bundle_output",
            "retranscribe_existing", "allow_low_quality",
            "device", "base_url", "model", "api_format", "chunk_minutes", "concurrency", "api_timeout", "remember_key",
        ):
            if key in data:
                self.vars[key].set(data[key])
        if "profile" not in data and data.get("verbatim"):
            self.vars["profile"].set("普通电影")
        if self.vars["profile"].get() in {"NSFW（保留语气声）", "NSFW－对白优先", "NSFW－完整声响"}:
            old_profile = self.vars["profile"].get()
            self.vars["profile"].set("普通电影")
            if old_profile in {"NSFW（保留语气声）", "NSFW－完整声响"}:
                self.vars["subtitle_content"].set("完整转录（包含语气声）")
        if self.vars["profile"].get() not in PROFILE_SETTINGS:
            self.vars["profile"].set("普通电影")
        if self.vars["subtitle_content"].get() not in {"对白优先（过滤拟声词）", "完整转录（包含语气声）"}:
            self.vars["subtitle_content"].set("完整转录（包含语气声）")
        if self.vars["performance_mode"].get() not in PERFORMANCE_SETTINGS:
            self.vars["performance_mode"].set("均衡（推荐）")
        legacy_languages = {"auto": "自动检测并整批锁定", "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语"}
        if self.vars["language"].get() in legacy_languages:
            self.vars["language"].set(legacy_languages[self.vars["language"].get()])
        if self.vars["language"].get() not in SOURCE_LANGUAGE_CODES:
            self.vars["language"].set("自动检测并整批锁定")
        if self.vars["target_language"].get() not in TARGET_LANGUAGES:
            self.vars["target_language"].set("简体中文")
        if self.vars["language_mode"].get() not in {"纯单语言（整批锁定）", "中外文混合（不锁定语言）"}:
            self.vars["language_mode"].set("纯单语言（整批锁定）")
        if data.get("remember_key") and isinstance(data.get("api_key"), str):
            self.vars["api_key"].set(data["api_key"])

    def save_settings(self):
        data = {
            key: value.get()
            for key, value in self.vars.items()
            if key != "api_key"
        }
        if self.vars["remember_key"].get():
            data["api_key"] = self.vars["api_key"].get()
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            temporary = CONFIG_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(CONFIG_FILE)
        except OSError as exc:
            self.messages.put(("log", f"设置保存失败：{exc}"))

    def on_close(self):
        self.save_settings()
        self.cancelled.set()
        self.destroy()

    def detect_gpu(self):
        try:
            import ctranslate2
            types = sorted(ctranslate2.get_supported_compute_types("cuda"))
            if not types:
                raise RuntimeError("CUDA 没有可用的计算类型")
            self.messages.put(("gpu", "GPU：CUDA 可用（" + ", ".join(types) + "），auto 将优先使用 GPU"))
        except Exception as exc:
            self.messages.put(("gpu", f"GPU：不可用，任务将使用 CPU int8（{exc}）"))

    def test_api(self):
        base_url = self.vars["base_url"].get().strip()
        model = self.vars["model"].get().strip()
        api_key = self.vars["api_key"].get()
        api_format = self.vars["api_format"].get()
        if not base_url or not model:
            messagebox.showerror(APP_TITLE, "请先填写中转站 Base URL 和模型名称。")
            return
        self.api_test_button.configure(state="disabled")
        self.status.configure(text="正在测试 API…")

        def worker():
            try:
                reply, used_url, protocol = test_chat_api(base_url, api_key, model, api_format)
                protocol_name = "Responses API" if protocol == "responses" else "Chat Completions"
                self.messages.put(("api_ok", f"API 可用\n接口格式：{protocol_name}\n实际接口：{used_url}\n模型响应：{reply[:100]}"))
            except Exception as exc:
                models = []
                try:
                    models, _ = fetch_models(base_url, api_key)
                except Exception:
                    pass
                self.messages.put(("api_error", (str(exc), models)))

        threading.Thread(target=worker, daemon=True).start()

    def load_models(self):
        base_url = self.vars["base_url"].get().strip()
        api_key = self.vars["api_key"].get()
        if not base_url:
            messagebox.showerror(APP_TITLE, "请先填写中转站 Base URL。")
            return
        self.models_button.configure(state="disabled")
        self.status.configure(text="正在获取中转站模型列表…")

        def worker():
            try:
                models, used_url = fetch_models(base_url, api_key)
                self.messages.put(("models_ok", (models, used_url)))
            except Exception as exc:
                self.messages.put(("models_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def open_output(self):
        path = Path(self.vars["output"].get().strip())
        if path.is_dir():
            os.startfile(path)
        else:
            messagebox.showinfo(APP_TITLE, "输出文件夹尚不存在。")

    def offer_environment_setup(self):
        if TRANSCRIBE_SCRIPT.is_file():
            return
        if messagebox.askyesno(
            APP_TITLE,
            "未找到 Whisper-GPT 环境。是否打开“首次配置与环境检测”选择路径并完成检查？",
        ):
            self.open_environment_setup()

    def open_environment_setup(self):
        setup_script = APP_DIR / "environment_setup.py"
        if not setup_script.is_file():
            messagebox.showerror(APP_TITLE, f"找不到环境配置助手：{setup_script}")
            return
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen([sys.executable, str(setup_script)], **kwargs)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法启动环境配置助手：{exc}")

    def start(self):
        input_dir = Path(self.vars["input"].get().strip())
        output_dir = Path(self.vars["output"].get().strip())
        if not input_dir.is_dir():
            messagebox.showerror(APP_TITLE, "请选择有效的媒体文件夹。")
            return
        if not any(
            self.vars[key].get()
            for key in ("output_original", "output_bilingual", "output_translated")
        ):
            messagebox.showerror(APP_TITLE, "请至少勾选一种需要生成的字幕版本。")
            return
        translation_requested = self.vars["output_bilingual"].get() or self.vars["output_translated"].get()
        try:
            chunk_minutes = int(self.vars["chunk_minutes"].get())
            concurrency = int(self.vars["concurrency"].get())
            api_timeout = int(self.vars["api_timeout"].get())
            if not 5 <= chunk_minutes <= 120 or not 1 <= concurrency <= 5 or not 60 <= api_timeout <= 900:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_TITLE, "每段分钟必须是 5–120，并发数必须是 1–5，超时必须是 60–900 秒。")
            return

        pattern = "**/*" if self.vars["recursive"].get() else "*"
        media_files = [
            path for path in input_dir.glob(pattern)
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
        ]
        if not media_files:
            messagebox.showerror(APP_TITLE, "所选文件夹中没有支持的音频或视频。")
            return
        existing_originals = 0
        for source in media_files:
            target_dir = media_output_dir(source, input_dir, output_dir, self.vars["bundle_output"].get())
            legacy_dir = output_dir / source.parent.relative_to(input_dir)
            if (
                (target_dir / f"{source.stem}_原声版.srt").exists()
                or (target_dir / f"{source.stem}_字幕.srt").exists()
                or (legacy_dir / f"{source.stem}_原声版.srt").exists()
                or (legacy_dir / f"{source.stem}_字幕.srt").exists()
            ):
                existing_originals += 1
        retranslate_existing = False
        if existing_originals and translation_requested and not self.vars["retranscribe_existing"].get():
            answer = messagebox.askyesnocancel(
                APP_TITLE,
                f"检测到 {existing_originals} 个视频已经有“原声版”字幕，将跳过 Whisper 转录。\n\n"
                "是否重新调用 API，为这些已有原声字幕生成/更新中外版和目标语言版？\n\n"
                "是：重新翻译；否：保留已有翻译，只处理新视频；取消：不开始任务。",
            )
            if answer is None:
                return
            retranslate_existing = answer
            if not answer and existing_originals == len(media_files) and not self.vars["bundle_output"].get():
                messagebox.showinfo(APP_TITLE, "所有媒体都已有原声版字幕，并且选择了不重新翻译，本次无需处理。")
                return
        elif (
            existing_originals == len(media_files)
            and self.vars["output_original"].get()
            and not self.vars["retranscribe_existing"].get()
            and not self.vars["bundle_output"].get()
        ):
            messagebox.showinfo(APP_TITLE, "所有媒体都已有原声版字幕，本次无需重复转录。")
            return
        needs_translation_api = translation_requested and (
            existing_originals < len(media_files)
            or retranslate_existing
            or self.vars["retranscribe_existing"].get()
        )
        if needs_translation_api and (not self.vars["base_url"].get().strip() or not self.vars["model"].get().strip()):
            messagebox.showerror(APP_TITLE, "请填写 API Base URL 和翻译模型名称。")
            return

        settings = {key: value.get() for key, value in self.vars.items()}
        settings["input"] = input_dir
        settings["output"] = output_dir
        settings["chunk_minutes"] = chunk_minutes
        settings["concurrency"] = concurrency
        settings["api_timeout"] = api_timeout
        settings["translation_requested"] = translation_requested
        settings["retranslate_existing"] = retranslate_existing
        self.save_settings()
        self.cancelled.clear()
        self.progress["value"] = 0
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.worker = threading.Thread(target=self.run_job, args=(settings,), daemon=True)
        self.worker.start()

    def stop(self):
        self.cancelled.set()
        self.status.configure(text="正在等待当前识别/请求结束后停止…")
        self.stop_button.configure(state="disabled")

    def run_job(self, settings):
        writer = QueueWriter(self.messages)
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = writer
        try:
            pattern = "**/*" if settings["recursive"] else "*"
            files = sorted(
                (p for p in settings["input"].glob(pattern) if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS),
                key=lambda p: str(p).lower(),
            )
            if not files:
                raise RuntimeError("所选文件夹中没有支持的音频或视频。")
            print(f"找到 {len(files)} 个媒体文件。")
            settings["output"].mkdir(parents=True, exist_ok=True)
            profile_name = settings["profile"] if settings["profile"] in PROFILE_SETTINGS else "普通电影"
            manifest = []
            for source in files:
                legacy_dir = settings["output"] / source.parent.relative_to(settings["input"])
                target_dir = media_output_dir(
                    source, settings["input"], settings["output"], settings["bundle_output"]
                )
                if settings["bundle_output"] and legacy_dir != target_dir:
                    migrate_legacy_outputs(
                        source, legacy_dir, target_dir, settings["target_language"], print
                    )
                original_srt = target_dir / f"{source.stem}_原声版.srt"
                legacy_srt = target_dir / f"{source.stem}_字幕.srt"
                if not original_srt.exists() and legacy_srt.exists():
                    target_dir.mkdir(parents=True, exist_ok=True)
                    write_single_language_srt(original_srt, parse_srt(legacy_srt))
                    print(f"[迁移] 已将旧版原文字幕作为原声版复用：{original_srt}")
                manifest.append((source, target_dir, original_srt))

            to_transcribe = [
                item for item in manifest
                if not item[2].exists() or settings["retranscribe_existing"]
            ]
            self.messages.put(("maximum", max(1, len(to_transcribe))))
            progress_value = 0
            translation_jobs = []

            for source, _target_dir, original_srt in manifest:
                if original_srt.exists():
                    if settings["retranscribe_existing"]:
                        print(f"[计划重新转录] 已有原声版将被更新：{original_srt}")
                    else:
                        print(f"[跳过转录] 已有原声版：{original_srt}")
                    if not settings["retranscribe_existing"] and settings["retranslate_existing"] and settings["translation_requested"]:
                        try:
                            cues = parse_srt(original_srt)
                            repaired_cues, timing_stats = repair_cue_timing(cues)
                            if timing_stats["clamped"]:
                                repaired_original = target_dir / f"{source.stem}_原声版_时间轴已修复.srt"
                                write_single_language_srt(repaired_original, repaired_cues)
                                print(
                                    f"[时间轴修复] {source.name}：截断异常长字幕 "
                                    f"{timing_stats['clamped']} 条；已保留原文件，并生成：{repaired_original}"
                                )
                            quality_ok, issues, metrics = analyze_asr_quality(
                                cues, enforce_coverage=profile_name in {"普通电影", "网课"}
                            )
                            print(f"ASR 质量检测 {source.name}: {metrics}")
                            if quality_ok or settings["allow_low_quality"]:
                                translation_jobs.append((source, original_srt, cues, True))
                            else:
                                warning = (
                                    f"{source.name} 原声字幕质量不合格，已阻止 API 翻译：\n"
                                    + "\n".join(f"- {issue}" for issue in issues)
                                    + "\n\n请勾选“重新转录已有原声版”，并修正语言模式/内容预设。"
                                )
                                print(warning)
                                self.messages.put(("notice", warning))
                        except Exception as exc:
                            print(f"[失败] 无法读取已有原声版 {original_srt.name}：{exc}")

            if to_transcribe:
                transcribe = load_transcribe_module()
                configured_ffmpeg = Path(str(load_project_config().get("ffmpeg_path", ""))).expanduser()
                if configured_ffmpeg.is_file():
                    transcribe.DEFAULT_FFMPEG_PATH = configured_ffmpeg.resolve()
                source_language = SOURCE_LANGUAGE_CODES.get(settings["language"])
                transcribe.LANGUAGE_OVERRIDE = source_language
                transcribe.WRITE_LEGACY_RAW = False
                if settings["language_mode"] == "中外文混合（不锁定语言）":
                    transcribe.CHUNK_SECONDS = 5 * 60
                    transcribe.CHUNK_OVERLAP_SECONDS = 5
                    print("混合语言模式：Whisper 分段调整为 5 分钟，每段重新检测语言。")
                else:
                    transcribe.CHUNK_SECONDS = 30 * 60
                    transcribe.CHUNK_OVERLAP_SECONDS = 10
                transcribe.OUTPUT_DIR = settings["output"]
                log_path = transcribe.setup_logging()
                print(f"日志文件：{log_path}")
                ffmpeg = transcribe.find_ffmpeg()
                ffprobe = transcribe.find_ffprobe(ffmpeg)
                print(f"FFmpeg：{ffmpeg or '未找到'}")

                device = str(settings["device"]).lower()
                if device == "cpu":
                    model = transcribe.create_model("cpu", "int8")
                    model_mode = "CPU"
                elif device == "gpu":
                    model = transcribe.create_model("cuda", "float16")
                    model_mode = "GPU"
                else:
                    try:
                        model = transcribe.create_model("cuda", "float16")
                        model_mode = "GPU"
                    except Exception as exc:
                        print(f"GPU 加载失败，自动改用 CPU int8：{exc}")
                        self.messages.put(("notice", f"GPU 加载失败，已自动切换到 CPU int8。\n\n原因：{exc}"))
                        model = transcribe.create_model("cpu", "int8")
                        model_mode = "CPU"
                print(f"模型加载完成：Whisper {transcribe.MODEL_NAME} / {model_mode}")
                model = ProfiledWhisperModel(
                    model, profile_name, source_language,
                    settings["language_mode"], settings["subtitle_content"], settings["performance_mode"],
                )
                profile = PROFILE_SETTINGS[profile_name]
                performance = PERFORMANCE_SETTINGS[settings["performance_mode"]]
                print(
                    f"内容预设：{profile_name}；性能模式：{settings['performance_mode']}；"
                    f"VAD={'开启' if performance['vad_filter'] else '关闭'}；"
                    f"beam_size={performance['beam_size']}；无语音阈值={profile['no_speech_threshold']}"
                )

            transcribed, translated, failed = 0, 0, 0
            print("\n========== 第一阶段：全部媒体转录 ==========")
            for number, (source, target_dir, original_srt) in enumerate(to_transcribe, 1):
                if self.cancelled.is_set():
                    raise InterruptedError("用户已停止任务")
                target_dir.mkdir(parents=True, exist_ok=True)
                transcribe.OUTPUT_DIR = target_dir
                self.messages.put(("status", f"转录 {number}/{len(to_transcribe)}：{source.name}"))
                try:
                    media_duration, _duration_error = transcribe.get_media_duration(source, ffprobe)
                    transcribe.process_file(model, source, number, len(to_transcribe), ffmpeg, ffprobe)
                    source_srt = target_dir / f"{source.stem}_字幕.srt"
                    cues = parse_srt(source_srt)
                    if settings["subtitle_content"] == "对白优先（过滤拟声词）":
                        cues, cleanup_stats = clean_dialogue_cues(cues)
                        print(
                            "对白优先净化："
                            f"输入 {cleanup_stats['input']} 条，删除无意义发声 {cleanup_stats['removed']} 条，"
                            f"合并连续重复 {cleanup_stats['merged']} 条，输出 {cleanup_stats['output']} 条。"
                        )
                        if not cues:
                            raise RuntimeError("对白净化后没有剩余有效字幕；若视频只有非语言发声，这是正常结果，可改用完整转录模式")
                    cues, timing_stats = repair_cue_timing(cues)
                    if timing_stats["clamped"]:
                        print(
                            f"时间轴修复：截断异常长字幕 {timing_stats['clamped']} 条，"
                            "避免空白画面持续显示上一句。"
                        )
                    enforce_coverage = profile_name in {"普通电影", "网课"}
                    quality_ok, issues, metrics = analyze_asr_quality(
                        cues, media_duration, enforce_coverage=enforce_coverage
                    )
                    print(f"ASR 质量检测 {source.name}: {metrics}")
                    if metrics.get("coverage_warning") and not enforce_coverage:
                        print(f"[提示，不阻止输出] {metrics['coverage_warning']}；当前内容预设允许对白较少。")
                    accepted = quality_ok or settings["allow_low_quality"]
                    if accepted:
                        if settings["output_original"] or settings["retranscribe_existing"]:
                            write_single_language_srt(original_srt, cues)
                        if settings["translation_requested"]:
                            translation_jobs.append((source, original_srt, cues, False))
                        transcribed += 1
                        if settings["output_original"] or settings["retranscribe_existing"]:
                            print(f"原声版字幕完成：{original_srt}")
                    else:
                        rejected_srt = target_dir / f"{source.stem}_质量不合格.srt"
                        write_single_language_srt(rejected_srt, cues)
                        failed += 1
                        warning = (
                            f"{source.name} 转录质量不合格，未覆盖已有原声版，也未调用 API：\n"
                            + "\n".join(f"- {issue}" for issue in issues)
                            + f"\n\n诊断字幕已保存：{rejected_srt}\n"
                            "请调整语言模式、原文语言或内容预设后重新转录。"
                        )
                        print(warning)
                        self.messages.put(("notice", warning))
                    source_srt.unlink(missing_ok=True)
                except InterruptedError:
                    raise
                except Exception as exc:
                    failed += 1
                    print(f"[转录失败] {source.name}：{exc}")
                    traceback.print_exc()
                progress_value += 1
                self.messages.put(("progress", progress_value))

            translation_chunks = sum(
                len(split_translation_batches(cues, settings["chunk_minutes"]))
                for _source, _original_srt, cues, _is_retranslation in translation_jobs
            )
            self.messages.put(("maximum", max(1, progress_value + translation_chunks)))
            print("\n========== 第二阶段：统一调用 API 翻译 ==========")
            for number, (source, original_srt, cues, is_retranslation) in enumerate(translation_jobs, 1):
                if self.cancelled.is_set():
                    raise InterruptedError("用户已停止任务")
                target_dir = original_srt.parent
                bilingual_srt = target_dir / f"{source.stem}_中外版.srt"
                translated_srt = target_dir / f"{source.stem}_{translated_suffix(settings['target_language'])}.srt"
                selected_existing = (
                    (settings["output_bilingual"] and bilingual_srt.exists())
                    or (settings["output_translated"] and translated_srt.exists())
                )
                if not is_retranslation and not settings["overwrite"] and selected_existing:
                    print(f"[跳过翻译] 已有目标字幕：{source.name}")
                    skipped_chunks = len(split_translation_batches(cues, settings["chunk_minutes"]))
                    progress_value += skipped_chunks
                    self.messages.put(("progress", progress_value))
                    continue
                self.messages.put(("status", f"翻译 {number}/{len(translation_jobs)}：{source.name}"))
                progress_box = [progress_value]
                cache_path = (
                    target_dir / ".translation_cache"
                    / f"{source.stem}_{translated_suffix(settings['target_language'])}.json"
                )
                try:
                    def advance_progress():
                        progress_box[0] += 1
                        self.messages.put(("progress", progress_box[0]))

                    translations = translate_cues_parallel(
                        cues, settings["base_url"], settings["api_key"], settings["model"], settings["api_format"],
                        settings["target_language"], settings["chunk_minutes"], settings["concurrency"],
                        settings["api_timeout"], self.cancelled, print, advance_progress,
                        media_name=source.name,
                        cache_path=cache_path,
                    )
                    progress_value = progress_box[0]
                    if settings["output_bilingual"]:
                        write_bilingual_srt(bilingual_srt, cues, translations)
                        print(f"中外版字幕完成：{bilingual_srt}")
                    if settings["output_translated"]:
                        write_single_language_srt(translated_srt, cues, translations)
                        print(f"目标语言版字幕完成：{translated_srt}")
                    translated += 1
                except InterruptedError:
                    raise
                except TranslationIncompleteError as exc:
                    failed += 1
                    print(f"[翻译部分完成] {exc}")
                    self.messages.put(("notice", str(exc)))
                except Exception as exc:
                    failed += 1
                    print(f"[翻译失败] {source.name}：{exc}")
                    traceback.print_exc()
                finally:
                    progress_value = progress_box[0]
            if settings["bundle_output"]:
                print("\n========== 第三阶段：整理同名播放文件夹 ==========")
                for source, target_dir, _original_srt in manifest:
                    if self.cancelled.is_set():
                        raise InterruptedError("用户已停止任务")
                    try:
                        bundle_media_outputs(source, target_dir, settings, print)
                    except Exception as exc:
                        failed += 1
                        print(f"[同名文件夹整理失败] {source.name}：{exc}")
            self.messages.put(("done", f"处理结束：新转录 {transcribed}，完成翻译 {translated}，失败 {failed}。"))
        except InterruptedError as exc:
            self.messages.put(("done", str(exc)))
        except Exception as exc:
            traceback.print_exc()
            self.messages.put(("error", str(exc)))
        finally:
            writer.flush()
            sys.stdout, sys.stderr = original_stdout, original_stderr

    def poll_messages(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", value + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "maximum":
                    self.progress.configure(maximum=value)
                elif kind == "progress":
                    self.progress["value"] = value
                elif kind == "status":
                    self.status.configure(text=value)
                elif kind == "gpu":
                    self.gpu_status.configure(text=value)
                elif kind == "notice":
                    messagebox.showwarning(APP_TITLE, value)
                elif kind == "api_ok":
                    self.api_test_button.configure(state="normal")
                    self.status.configure(text="API 测试成功")
                    messagebox.showinfo(APP_TITLE, value)
                elif kind == "api_error":
                    self.api_test_button.configure(state="normal")
                    self.status.configure(text="API 测试失败")
                    error_text, models = value
                    if models:
                        self.model_box.configure(values=models)
                        error_text += "\n\n已自动取得可用模型，请在模型下拉框中重新选择。"
                    messagebox.showerror(APP_TITLE, "API 测试失败：\n\n" + error_text)
                elif kind == "models_ok":
                    models, used_url = value
                    self.models_button.configure(state="normal")
                    self.model_box.configure(values=models)
                    self.status.configure(text=f"已获取 {len(models)} 个模型")
                    messagebox.showinfo(APP_TITLE, f"已获取 {len(models)} 个模型。\n接口：{used_url}\n\n请在模型下拉框中选择。")
                elif kind == "models_error":
                    self.models_button.configure(state="normal")
                    self.status.configure(text="获取模型失败")
                    messagebox.showerror(APP_TITLE, value)
                elif kind in {"done", "error"}:
                    self.status.configure(text=value)
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    (messagebox.showinfo if kind == "done" else messagebox.showerror)(APP_TITLE, value)
        except queue.Empty:
            pass
        self.after(100, self.poll_messages)


if __name__ == "__main__":
    try:
        enable_windows_dpi_awareness()
        App().mainloop()
    except Exception:
        error_text = traceback.format_exc()
        error_log = Path(__file__).with_name("startup_error.log")
        try:
            error_log.write_text(error_text, encoding="utf-8")
        except OSError:
            pass
        try:
            fallback = tk.Tk()
            fallback.withdraw()
            messagebox.showerror(
                APP_TITLE,
                f"程序启动失败，错误记录已写入：\n{error_log}\n\n{error_text[-1200:]}",
            )
            fallback.destroy()
        except Exception:
            pass
        raise
