"""FFmpeg runner for cleaner jobs."""
import subprocess
import threading
import time
from pathlib import Path

import sys

from pipeline.core.media import ffprobe_duration, nvenc_available
from pipeline.cleaner.cleaner_jobs import (
    update_job,
    register_proc,
    unregister_proc,
    get_job,
)

CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) if sys.platform == "win32" else 0

def run_cleaner_job_sync(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
        
    input_path = job["input_path"]
    output_path = job["output_path"]
    method = job["method"]
    options = job["options"]
    
    update_job(job_id, {"status": "processing", "startedAt": time.time(), "progress": 0})
    
    try:
        duration_s = ffprobe_duration(input_path) or 100.0
        
        cmd = ["ffmpeg", "-y", "-i", input_path]
        
        # Build command
        if method == "metadata":
            if options.get("removeVideoMeta") or options.get("removeAudioMeta") or options.get("removeContainerMeta"):
                cmd.extend(["-map_metadata", "-1"])
            if options.get("removeChapters"):
                cmd.extend(["-map_chapters", "-1"])
            cmd.extend(["-c", "copy"])
        
        elif method == "reencode":
            vcodec = options.get("videoCodec", "libx264")
            acodec = options.get("audioMode", "copy")
            crf = str(options.get("crf", 23))
            preset = options.get("preset", "fast")
            
            if vcodec == "copy":
                cmd.extend(["-c:v", "copy"])
            elif vcodec == "libx264" and nvenc_available():
                # Mặc định libx264 → dùng GPU khi có (crf ↦ cq tương đương)
                cmd.extend(["-c:v", "h264_nvenc", "-preset", "p5",
                            "-rc", "vbr", "-cq", crf, "-b:v", "0"])
            else:
                cmd.extend(["-c:v", vcodec, "-preset", preset, "-crf", crf])
                
            cmd.extend(["-c:a", acodec])
            
            if options.get("faststart"):
                cmd.extend(["-movflags", "+faststart"])
            
            if options.get("removeVideoMeta") or options.get("removeAudioMeta") or options.get("removeContainerMeta"):
                cmd.extend(["-map_metadata", "-1"])
                
        elif method == "optimize":
            if nvenc_available():
                cmd.extend(["-c:v", "h264_nvenc", "-preset", "p4",
                            "-rc", "vbr", "-cq", "26", "-b:v", "0"])
            else:
                cmd.extend(["-c:v", "libx264", "-preset", "faster", "-crf", "26"])
            cmd.extend(["-movflags", "+faststart", "-c:a", "aac"])
            
        cmd.append(output_path)
        
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        register_proc(job_id, proc)
        
        if proc.stderr:
            for line in proc.stderr:
                if "time=" in line:
                    try:
                        time_str = line.split("time=")[1].split()[0]
                        parts = time_str.split(":")
                        if len(parts) == 3:
                            h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                            current_s = h * 3600 + m * 60 + s
                            progress = min(99.0, (current_s / duration_s) * 100)
                            update_job(job_id, {"progress": progress})
                    except Exception:
                        pass
        
        proc.wait()
        unregister_proc(job_id)
        
        # Check if cancelled during run
        current_job = get_job(job_id)
        if current_job and current_job.get("status") == "cancelled":
            return
            
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg exit code {proc.returncode}")
            
        # Success
        out_size = Path(output_path).stat().st_size if Path(output_path).exists() else 0
        update_job(job_id, {
            "status": "done",
            "progress": 100.0,
            "outputSize": out_size,
            "finishedAt": time.time()
        })
        
    except Exception as e:
        unregister_proc(job_id)
        current_job = get_job(job_id)
        if current_job and current_job.get("status") != "cancelled":
            update_job(job_id, {
                "status": "error",
                "error": str(e),
                "finishedAt": time.time()
            })

def start_cleaner_job(job_id: str) -> None:
    threading.Thread(
        target=run_cleaner_job_sync,
        args=(job_id,),
        name=f"cleaner-{job_id}",
        daemon=True
    ).start()
