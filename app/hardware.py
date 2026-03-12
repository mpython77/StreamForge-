"""
StreamForge Hardware Detection + FFmpeg Pipeline — ULTIMATE Architecture

CPU Optimization:
- x264 advanced: aq-mode=3, rc-lookahead=40, subme=9, me=umh, trellis=2
- sliced-threads for HLS (parallel slice encoding per frame)
- Thread allocation: decode threads + encode threads optimally split
- force_key_frames expression for exact segment alignment

GPU Optimization:
- NVENC: preset p5 (slower but better quality), spatial-aq=1, temporal-aq=1
- Hardware decode pipeline: -hwaccel cuda -hwaccel_output_format cuda
- Direct GPU memory for decode->encode pipeline

HLS Compliance:
- Constrained VBR: maxrate=1.5x, bufsize=2x
- sc_threshold=0 + force_key_frames for perfect segment alignment
- CODECS string in master playlist
- independent_segments flag
"""

import subprocess
import os
import re
import platform
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class HardwareInfo:
    cpu_name: str = "CPU"
    cpu_cores: int = 4
    cpu_threads: int = 8
    recommended_threads: int = 7
    gpu_name: Optional[str] = None
    gpu_vendor: Optional[str] = None
    gpu_available: bool = False
    gpu_encoders: list = field(default_factory=list)
    ffmpeg_version: str = "unknown"
    best_encoder: str = "libx264"
    best_mode: str = "cpu"
    speed_estimate: str = "1x (CPU)"


_hw_cache: Optional[HardwareInfo] = None


def detect_hardware() -> HardwareInfo:
    global _hw_cache
    if _hw_cache:
        return _hw_cache

    cpu = _detect_cpu()
    ff_ver = _get_ffmpeg_version()
    gpu_encs = _detect_gpu_encoders()
    gpu = _detect_gpu()

    available_gpu = [e for e in gpu_encs if e["available"]]
    best_encoder = "libx264"
    best_mode = "cpu"
    speed_est = f"1x (CPU {cpu['cores']}c/{cpu['threads']}t)"

    if available_gpu:
        for vendor_pref in ["NVIDIA", "AMD", "Intel"]:
            for enc in available_gpu:
                if enc["vendor"] == vendor_pref and enc["codec"] == "h264":
                    best_encoder = enc["name"]
                    best_mode = "gpu"
                    speed_est = f"3-5x ({vendor_pref} GPU)"
                    break
            if best_mode == "gpu":
                break

    hw = HardwareInfo(
        cpu_name=cpu["name"],
        cpu_cores=cpu["cores"],
        cpu_threads=cpu["threads"],
        recommended_threads=max(1, cpu["cores"] - 1),
        gpu_name=gpu.get("name"),
        gpu_vendor=gpu.get("vendor"),
        gpu_available=len(available_gpu) > 0,
        gpu_encoders=gpu_encs,
        ffmpeg_version=ff_ver,
        best_encoder=best_encoder,
        best_mode=best_mode,
        speed_estimate=speed_est,
    )
    _hw_cache = hw
    return hw


def _detect_cpu() -> dict:
    total = os.cpu_count() or 4
    system = platform.system()

    # Windows: wmic
    if system == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/format:list"],
                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace"
            )
            data = {}
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
            if data:
                return {
                    "name": data.get("Name", "CPU"),
                    "cores": int(data.get("NumberOfCores", total // 2)),
                    "threads": int(data.get("NumberOfLogicalProcessors", total)),
                }
        except Exception:
            pass

    # Linux: /proc/cpuinfo
    elif system == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                cpuinfo = f.read()
            name_match = re.search(r"model name\s*:\s*(.+)", cpuinfo)
            name = name_match.group(1).strip() if name_match else "CPU"
            # Physical cores from unique core IDs
            core_ids = set(re.findall(r"core id\s*:\s*(\d+)", cpuinfo))
            cores = len(core_ids) if core_ids else max(1, total // 2)
            return {"name": name, "cores": cores, "threads": total}
        except Exception:
            pass

    # macOS: sysctl
    elif system == "Darwin":
        try:
            name_r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                    capture_output=True, text=True, timeout=5)
            cores_r = subprocess.run(["sysctl", "-n", "hw.physicalcpu"],
                                     capture_output=True, text=True, timeout=5)
            name = name_r.stdout.strip() or "CPU"
            cores = int(cores_r.stdout.strip()) if cores_r.stdout.strip() else max(1, total // 2)
            return {"name": name, "cores": cores, "threads": total}
        except Exception:
            pass

    return {"name": "CPU", "cores": max(1, total // 2), "threads": total}


def _detect_gpu() -> dict:
    # Try nvidia-smi first (cross-platform for NVIDIA)
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            return {"name": parts[0], "vendor": "NVIDIA",
                    "driver": parts[1] if len(parts) > 1 else "",
                    "memory_mb": int(parts[2]) if len(parts) > 2 else 0}
    except Exception:
        pass

    system = platform.system()

    # Windows: wmic
    if system == "Windows":
        try:
            r = subprocess.run(
                ["wmic", "path", "win32_videocontroller", "get", "Name", "/format:list"],
                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace"
            )
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("Name="):
                    name = line.split("=", 1)[1].strip()
                    if name:
                        return {"name": name, "vendor": _classify_gpu_vendor(name)}
        except Exception:
            pass

    # Linux: lspci
    elif system == "Linux":
        try:
            r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if "VGA" in line or "3D" in line or "Display" in line:
                    name = line.split(":", 2)[-1].strip() if ":" in line else line
                    return {"name": name, "vendor": _classify_gpu_vendor(name)}
        except Exception:
            pass

    # macOS: system_profiler
    elif system == "Darwin":
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10
            )
            match = re.search(r"Chipset Model:\s*(.+)", r.stdout)
            if match:
                name = match.group(1).strip()
                return {"name": name, "vendor": _classify_gpu_vendor(name)}
        except Exception:
            pass

    return {"name": None, "vendor": None}


def _classify_gpu_vendor(name: str) -> str:
    """Classify GPU vendor from device name"""
    nl = name.lower()
    if any(k in nl for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "NVIDIA"
    elif any(k in nl for k in ("amd", "radeon", "rx ")):
        return "AMD"
    elif any(k in nl for k in ("intel", "uhd", "iris", "arc")):
        return "Intel"
    elif any(k in nl for k in ("apple", "m1", "m2", "m3", "m4")):
        return "Apple"
    return "Unknown"


def _detect_gpu_encoders() -> list:
    check_list = [
        ("h264_nvenc", "NVIDIA", "h264", "NVENC H.264"),
        ("hevc_nvenc", "NVIDIA", "hevc", "NVENC H.265"),
        ("h264_amf", "AMD", "h264", "AMF H.264"),
        ("hevc_amf", "AMD", "hevc", "AMF H.265"),
        ("h264_qsv", "Intel", "h264", "QSV H.264"),
        ("hevc_qsv", "Intel", "hevc", "QSV H.265"),
    ]
    available = set()
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=10)
        for line in r.stdout.split("\n"):
            for name, _, _, _ in check_list:
                if f" {name} " in line or line.strip().startswith(name):
                    available.add(name)
    except Exception:
        pass

    results = []
    for name, vendor, codec, label in check_list:
        is_avail = name in available and _test_encoder(name)
        results.append({"name": name, "vendor": vendor, "codec": codec,
                        "available": is_avail, "label": label})
    return results


def _test_encoder(name: str) -> bool:
    try:
        null_dev = os.devnull  # 'NUL' on Windows, '/dev/null' on Unix
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
             "-c:v", name, "-frames:v", "1", "-f", "null", null_dev],
            capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except Exception:
        return False


def _get_ffmpeg_version() -> str:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        m = re.search(r"ffmpeg version (\S+)", r.stdout.split("\n")[0])
        return m.group(1) if m else "unknown"
    except Exception:
        return "not installed"


# ═══════════════════════════════════════════════════════════
# GPU SESSION LIMITS — NVENC hardware limits
# ═══════════════════════════════════════════════════════════

# NVIDIA consumer GPU: max 3-5 simultaneous NVENC sessions
# Professional (Quadro/A-series): unlimited
MAX_NVENC_SESSIONS = 3
MAX_AMF_SESSIONS = 4
MAX_QSV_SESSIONS = 4


def get_max_gpu_sessions(encoder_name: str) -> int:
    """Returns max concurrent sessions for GPU encoders"""
    if "nvenc" in encoder_name:
        return MAX_NVENC_SESSIONS
    if "amf" in encoder_name:
        return MAX_AMF_SESSIONS
    if "qsv" in encoder_name:
        return MAX_QSV_SESSIONS
    return 999  # CPU — no limit


def is_gpu_encoder(encoder_name: str) -> bool:
    """Encoder GPU mi yoki CPU mi"""
    return encoder_name not in ("libx264", "libx265")


def get_hwaccel_args(encoder_name: str) -> list[str]:
    """GPU decode pipeline argumentlari — zero-copy"""
    if "nvenc" in encoder_name:
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if "qsv" in encoder_name:
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
    if "amf" in encoder_name:
        return ["-hwaccel", "d3d11va"]
    return []


def get_scale_filter(encoder_name: str, height: int) -> str:
    """Correct scale filter for GPU or CPU"""
    if "nvenc" in encoder_name:
        return f"scale_cuda=-2:{height}"       # GPU RAM da scale (zero-copy)
    if "qsv" in encoder_name:
        return f"scale_qsv=w=-2:h={height}"   # QSV hardware scale
    return f"scale=-2:{height}:flags=lanczos"  # CPU — eng sifatli


# ═══════════════════════════════════════════════════════════
# ENCODER ARGS — Maximum CPU/GPU utilization
# ═══════════════════════════════════════════════════════════

def get_encoder_args(
    encoder_name: str,
    bitrate: str,
    preset: str = "medium",
    fps: float = 30,
    segment_duration: int = 4,
    threads: int = 0,
    total_threads: int = 0,
) -> list[str]:
    """
    ULTIMATE FFmpeg encoder argumentlari.

    CPU: x264 advanced params — aq-mode=3, rc-lookahead, subme, me, trellis,
         sliced-threads for per-frame parallel, thread allocation
    GPU: preset p5, spatial-aq, temporal-aq, lookahead
    
    Constrained VBR: maxrate = 1.5x, bufsize = 2x
    """
    br_k = int(bitrate.replace("k", ""))
    maxrate = f"{int(br_k * 1.5)}k"
    bufsize = f"{int(br_k * 2)}k"
    gop = int(segment_duration * fps)

    # ═══ NVIDIA NVENC H.264 — Zero-copy pipeline ═══
    if encoder_name == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p5",                    # p5 = best quality/speed balance
            "-tune", "hq",                      # High quality tuning
            "-profile:v", "high",
            "-level:v", "4.1",
            "-rc", "vbr",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-spatial-aq", "1",                  # Frame ichidagi AQ
            "-temporal-aq", "1",                 # Framelar orasidagi AQ
            "-aq-strength", "8",
            "-rc-lookahead", "32",
            "-multipass", "2",                   # 2-pass encode (GPU da tez)
            "-weighted_pred", "1",               # Weighted prediction
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-bf", "3",
            "-b_ref_mode", "middle",             # B-frame reference mode
            "-gpu", "0",
            "-extra_hw_frames", "36",            # lookahead + bf + 1
        ]

    # ═══ NVIDIA NVENC HEVC — Zero-copy pipeline ═══
    if encoder_name == "hevc_nvenc":
        return [
            "-c:v", "hevc_nvenc",
            "-preset", "p5",
            "-tune", "hq",
            "-profile:v", "main",
            "-tier", "main",
            "-rc", "vbr",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-spatial-aq", "1",
            "-temporal-aq", "1",
            "-aq-strength", "8",
            "-rc-lookahead", "32",
            "-multipass", "2",
            "-weighted_pred", "1",
            "-tag:v", "hvc1",                    # Required for Apple HLS
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-bf", "3",
            "-b_ref_mode", "middle",
            "-gpu", "0",
            "-extra_hw_frames", "36",
        ]

    # ═══ AMD AMF H.264 ═══
    if encoder_name == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-quality", "balanced",
            "-profile:v", "high",
            "-rc", "vbr_peak",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-preanalysis", "true",              # Pre-analysis pass
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-bf", "3",
        ]

    # ═══ AMD AMF HEVC ═══
    if encoder_name == "hevc_amf":
        return [
            "-c:v", "hevc_amf",
            "-quality", "balanced",
            "-rc", "vbr_peak",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-tag:v", "hvc1",
            "-g", str(gop),
            "-keyint_min", str(gop),
        ]

    # ═══ Intel QSV H.264 ═══
    if encoder_name == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-preset", "medium",
            "-profile:v", "high",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-look_ahead", "1",                  # Lookahead mode
            "-look_ahead_depth", "20",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-bf", "3",
        ]

    # ═══ Intel QSV HEVC ═══
    if encoder_name == "hevc_qsv":
        return [
            "-c:v", "hevc_qsv",
            "-preset", "medium",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-tag:v", "hvc1",
            "-look_ahead", "1",
            "-look_ahead_depth", "20",
            "-g", str(gop),
            "-keyint_min", str(gop),
        ]

    # ═══ CPU — libx264/libx265 PRESET-PROPORTIONAL ═══
    speed_map = {
        "ultrafast": "ultrafast", "veryfast": "veryfast", "faster": "faster",
        "fast": "fast", "medium": "medium", "slow": "slow",
        "slower": "slower", "veryslow": "veryslow",
    }

    # Thread allocation
    if threads <= 0:
        enc_threads = total_threads or (os.cpu_count() or 8)
    else:
        enc_threads = threads

    # ─── HEVC / H.265 (libx265) ───
    if encoder_name == "libx265":
        x265_preset = speed_map.get(preset, "medium")
        x265_params = [
            f"pools={enc_threads}",
            f"frame-threads={min(4, enc_threads)}",
            f"keyint={gop}",
            f"min-keyint={gop}",
            "scenecut=0",
            "open-gop=0",
            "repeat-headers=1",    # Required for HLS — each segment must be independent
        ]

        if x265_preset in ("ultrafast", "veryfast"):
            x265_params.extend([
                "bframes=0", "ref=1", "aq-mode=0", "rc-lookahead=5",
                "rd=2", "no-sao=1",
            ])
        elif x265_preset in ("faster", "fast"):
            x265_params.extend([
                "bframes=3", "ref=2", "aq-mode=1",
                f"rc-lookahead={min(15, gop)}", "rd=3",
            ])
        elif x265_preset == "medium":
            x265_params.extend([
                "bframes=4", "ref=3", "aq-mode=2",
                f"rc-lookahead={min(20, gop)}", "rd=4",
            ])
        elif x265_preset == "slow":
            x265_params.extend([
                "bframes=4", "b-adapt=2", "ref=4", "aq-mode=2",
                f"rc-lookahead={min(30, gop)}", "rd=5",
                "psy-rd=2.0", "psy-rdoq=1.0",
            ])
        else:
            x265_params.extend([
                "bframes=5", "b-adapt=2", "ref=5", "aq-mode=3",
                f"rc-lookahead={min(40, gop)}", "rd=6",
                "psy-rd=2.0", "psy-rdoq=1.0", "tu-intra-depth=3",
                "tu-inter-depth=3", "limit-tu=0",
            ])

        return [
            "-c:v", "libx265",
            "-preset", x265_preset,
            "-profile:v", "main",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-tag:v", "hvc1",         # Required tag for Apple HLS
            "-x265-params", ":".join(x265_params),
        ]

    # ─── H.264 / libx264 ───
    x264_preset = speed_map.get(preset, "medium")

    x264_params = [
        f"threads={enc_threads}",
        f"keyint={gop}",
        f"min-keyint={gop}",
        "scenecut=0",
    ]

    if x264_preset in ("ultrafast", "veryfast"):
        x264_params.extend([
            "bframes=0", "ref=1", "aq-mode=1",
            "rc-lookahead=0", "subme=1", "me=dia", "trellis=0",
        ])
    elif x264_preset in ("faster", "fast"):
        x264_params.extend([
            "bframes=2", "ref=2", "aq-mode=1",
            f"rc-lookahead={min(20, gop)}", "subme=4", "me=hex", "trellis=0",
        ])
    elif x264_preset == "medium":
        x264_params.extend([
            "bframes=2", "b-adapt=1", "ref=2", "aq-mode=1",
            f"rc-lookahead={min(30, gop)}", "subme=6", "me=hex", "trellis=0",
        ])
    elif x264_preset == "slow":
        x264_params.extend([
            "bframes=3", "b-adapt=2", "ref=3", "aq-mode=2", "aq-strength=0.8",
            f"rc-lookahead={min(40, gop)}", "subme=7", "me=hex",
            "trellis=1", "psy-rd=1.0:0.15", "qcomp=0.6",
        ])
    else:
        x264_params.extend([
            "bframes=3", "b-adapt=2", "ref=4", "aq-mode=3", "aq-strength=0.8",
            f"rc-lookahead={min(60, gop)}", "subme=9", "me=umh",
            "trellis=2", "direct=auto", "psy-rd=1.0:0.15",
            "deblock=-1:-1", "qcomp=0.6",
        ])

    return [
        "-c:v", "libx264",
        "-preset", x264_preset,
        "-profile:v", "high",
        "-level:v", "4.1",
        "-b:v", bitrate,
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-x264-params", ":".join(x264_params),
    ]


# ═══════════════════════════════════════════════════════════
# CODEC CONSTANTS — for HLS playlists
# ═══════════════════════════════════════════════════════════

CODEC_INFO = {
    "libx264": {"codecs_string": "avc1.640029,mp4a.40.2", "label": "H.264", "ext": "ts"},
    "libx265": {"codecs_string": "hvc1.1.6.L93.B0,mp4a.40.2", "label": "H.265/HEVC", "ext": "ts"},
    "h264_nvenc": {"codecs_string": "avc1.640029,mp4a.40.2", "label": "H.264 NVENC", "ext": "ts"},
    "hevc_nvenc": {"codecs_string": "hvc1.1.6.L93.B0,mp4a.40.2", "label": "H.265 NVENC", "ext": "ts"},
    "h264_amf": {"codecs_string": "avc1.640029,mp4a.40.2", "label": "H.264 AMF", "ext": "ts"},
    "hevc_amf": {"codecs_string": "hvc1.1.6.L93.B0,mp4a.40.2", "label": "H.265 AMF", "ext": "ts"},
    "h264_qsv": {"codecs_string": "avc1.640029,mp4a.40.2", "label": "H.264 QSV", "ext": "ts"},
    "hevc_qsv": {"codecs_string": "hvc1.1.6.L93.B0,mp4a.40.2", "label": "H.265 QSV", "ext": "ts"},
}
