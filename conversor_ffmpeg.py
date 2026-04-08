import os
import queue
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

SUFFIX = " @bolivia.news4 #LaPazBolivia #Bolivia #BoliviaNewsBO465"
TARGETS = [("1.1", 1.1), ("1.2", 1.2)]
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".wmv",
    ".flv",
    ".ts",
    ".mpeg",
    ".mpg",
}
MAX_WORKERS = max(1, min(3, os.cpu_count() or 1))


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def has_ffmpeg() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def detect_audio_stream(video_path: Path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def output_name_for(video_path: Path) -> str:
    ext = video_path.suffix if video_path.suffix else ".mp4"
    return f"{video_path.stem}{SUFFIX}{ext}"


def ffmpeg_command(input_file: Path, output_file: Path, speed: float, with_audio: bool):
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_file),
        "-vf",
        f"setpts=PTS/{speed}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-movflags",
        "+faststart",
    ]
    if with_audio:
        cmd.extend(["-af", f"atempo={speed}", "-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.append("-an")
    cmd.extend(["-sn", str(output_file)])
    return cmd


def run_ffmpeg(input_file: Path, output_file: Path, speed: float):
    audio_state = detect_audio_stream(input_file)

    def run_once(with_audio: bool):
        cmd = ffmpeg_command(input_file, output_file, speed, with_audio)
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode == 0, (result.stderr or "").strip()

    if audio_state is True:
        return run_once(True)
    if audio_state is False:
        return run_once(False)

    ok, err = run_once(True)
    if ok:
        return ok, err
    ok2, err2 = run_once(False)
    if ok2:
        return ok2, err2
    return False, err2 or err


class ConversorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Conversor FFmpeg")
        self.root.geometry("860x560")
        self.root.minsize(760, 500)

        self.base_dir = Path(__file__).resolve().parent
        self.input_dir = self.base_dir / "VIDEOS ORIGINALES"
        self.output_root = self.base_dir / "VIDEOS CONVERTIDOS"

        self.event_queue = queue.Queue()
        self.running = False
        self.total_jobs = 0
        self.done_jobs = 0
        self.ok_jobs = 0
        self.fail_jobs = 0

        self.status_var = tk.StringVar(value="Listo para convertir")
        self.counter_var = tk.StringVar(value="0/0")
        self.source_var = tk.StringVar(value=f"Entrada: {self.input_dir}")
        self.dest_var = tk.StringVar(value=f"Salida: {self.output_root}")

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self.on_event)

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text="Conversor 1.1x y 1.2x", font=("Segoe UI", 15, "bold"))
        title.pack(anchor="w")

        ttk.Label(frame, textvariable=self.source_var).pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, textvariable=self.dest_var).pack(anchor="w")

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(12, 8))

        self.start_button = ttk.Button(controls, text="Iniciar", command=self.start)
        self.start_button.pack(side="left")

        self.counter_label = ttk.Label(controls, textvariable=self.counter_var, font=("Segoe UI", 10, "bold"))
        self.counter_label.pack(side="right")

        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill="x")

        self.status_label = ttk.Label(frame, textvariable=self.status_var)
        self.status_label.pack(anchor="w", pady=(8, 8))

        self.log_box = ScrolledText(frame, height=18, font=("Consolas", 10), state="disabled")
        self.log_box.pack(fill="both", expand=True)

    def append_log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def collect_videos(self):
        if not self.input_dir.exists():
            return []
        return sorted([p for p in self.input_dir.iterdir() if is_video_file(p)], key=lambda x: x.name.lower())

    def start(self):
        if self.running:
            return
        if not has_ffmpeg():
            messagebox.showerror("Error", "No se encontro ffmpeg en PATH.")
            self.status_var.set("Error: ffmpeg no disponible")
            return

        videos = self.collect_videos()
        for folder_name, _ in TARGETS:
            (self.output_root / folder_name).mkdir(parents=True, exist_ok=True)

        if not videos:
            self.status_var.set("No hay videos para convertir")
            messagebox.showinfo("Sin videos", "No se encontraron videos en VIDEOS ORIGINALES.")
            return

        jobs = []
        for video in videos:
            for folder_name, speed in TARGETS:
                jobs.append((video, folder_name, speed))

        self.total_jobs = len(jobs)
        self.done_jobs = 0
        self.ok_jobs = 0
        self.fail_jobs = 0
        self.running = True

        self.progress.configure(maximum=self.total_jobs, value=0)
        self.counter_var.set(f"0/{self.total_jobs}")
        self.status_var.set(f"Procesando {len(videos)} video(s) con {MAX_WORKERS} proceso(s) en paralelo")
        self.start_button.configure(state="disabled")
        self.clear_log()
        self.append_log(f"Videos detectados: {len(videos)}")
        self.append_log(f"Tareas totales: {self.total_jobs}")

        worker = threading.Thread(target=self.worker, args=(jobs,), daemon=True)
        worker.start()

    def convert_one(self, job):
        video, folder_name, speed = job
        out_dir = self.output_root / folder_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = output_name_for(video)
        out_file = out_dir / out_name

        ok, err = run_ffmpeg(video, out_file, speed)
        speed_label = folder_name + "x"
        line = f"[OK] {speed_label} {video.name}" if ok else f"[ERROR] {speed_label} {video.name}"
        return {"ok": ok, "line": line, "error": err}

    def worker(self, jobs):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(self.convert_one, job) for job in jobs]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as ex:
                    result = {"ok": False, "line": "[ERROR] Falla interna", "error": str(ex)}
                self.event_queue.put(("job", result))
        self.event_queue.put(("done", None))

    def on_event(self):
        while True:
            try:
                kind, data = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "job":
                self.done_jobs += 1
                if data["ok"]:
                    self.ok_jobs += 1
                else:
                    self.fail_jobs += 1

                self.progress.configure(value=self.done_jobs)
                self.counter_var.set(f"{self.done_jobs}/{self.total_jobs}")
                self.status_var.set(f"Procesando... {self.done_jobs}/{self.total_jobs}")
                self.append_log(data["line"])

                if not data["ok"] and data["error"]:
                    detail = data["error"].splitlines()[-1]
                    self.append_log("    " + detail)

            if kind == "done":
                self.running = False
                self.start_button.configure(state="normal")
                self.status_var.set("Conversion terminada")
                self.append_log(f"Finalizado. OK: {self.ok_jobs} | Errores: {self.fail_jobs}")
                if self.fail_jobs == 0:
                    messagebox.showinfo("Listo", "Proceso completado sin errores.")
                else:
                    messagebox.showwarning("Finalizado", f"Proceso completado con {self.fail_jobs} error(es).")

        self.root.after(120, self.on_event)

    def close(self):
        if self.running:
            leave = messagebox.askyesno("Salir", "Hay conversiones en curso. Deseas salir?")
            if not leave:
                return
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ConversorApp().run()
