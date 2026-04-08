import json
import os
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

TEMPLATE_CONFIG_FILENAME = "plantillas.json"
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


def output_name_for(video_path: Path, suffix: str) -> str:
    ext = video_path.suffix if video_path.suffix else ".mp4"
    clean_suffix = suffix.strip()
    if clean_suffix:
        return f"{video_path.stem} {clean_suffix}{ext}"
    return f"{video_path.stem}{ext}"


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
        self.template_config_path = self.base_dir / TEMPLATE_CONFIG_FILENAME
        self.templates, self.active_template = self.load_templates()

        self.event_queue = queue.Queue()
        self.running = False
        self.total_jobs = 0
        self.done_jobs = 0
        self.ok_jobs = 0
        self.fail_jobs = 0
        self.started_at = None
        self.timer_after_id = None

        self.status_var = tk.StringVar(value="Listo para convertir")
        self.counter_var = tk.StringVar(value="0/0")
        self.timer_var = tk.StringVar(value="Tiempo total: 00:00:00")
        self.source_var = tk.StringVar(value=f"Entrada: {self.input_dir}")
        self.dest_var = tk.StringVar(value=f"Salida: {self.output_root}")
        self.template1_var = tk.StringVar(value=self.templates[0])
        self.template2_var = tk.StringVar(value=self.templates[1])

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self.on_event)

    def load_templates(self):
        templates = ["", ""]
        active_template = 1
        if not self.template_config_path.exists():
            try:
                self.write_templates_file(templates, active_template)
            except OSError:
                pass
            return templates, active_template

        try:
            data = json.loads(self.template_config_path.read_text(encoding="utf-8"))
            loaded_templates = data.get("templates")
            if isinstance(loaded_templates, list):
                if len(loaded_templates) > 0 and isinstance(loaded_templates[0], str):
                    templates[0] = loaded_templates[0].strip()
                if len(loaded_templates) > 1 and isinstance(loaded_templates[1], str):
                    templates[1] = loaded_templates[1].strip()
            loaded_active = data.get("active_template")
            if loaded_active in (1, 2):
                active_template = loaded_active
        except Exception:
            pass
        try:
            self.write_templates_file(templates, active_template)
        except OSError:
            pass
        return templates, active_template

    def write_templates_file(self, templates, active_template):
        data = {
            "templates": templates,
            "active_template": active_template,
        }
        self.template_config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def sync_templates_from_ui(self, notify=True):
        template_1 = self.template1_var.get().strip()
        template_2 = self.template2_var.get().strip()
        if not template_1 or not template_2:
            messagebox.showerror("Error", "Las dos plantillas son obligatorias.")
            return False

        selected_index = self.template_selector.current()
        if selected_index not in (0, 1):
            selected_index = 0
            self.template_selector.current(0)

        self.templates = [template_1, template_2]
        self.active_template = selected_index + 1

        try:
            self.write_templates_file(self.templates, self.active_template)
        except OSError as ex:
            messagebox.showerror("Error", f"No se pudo guardar plantillas.json: {ex}")
            return False

        if notify:
            self.status_var.set(f"Plantillas guardadas. Activa: {self.active_template}")
            self.append_log(f"Plantillas guardadas. Activa: {self.active_template}")
        return True

    def on_template_selected(self, _event=None):
        self.sync_templates_from_ui(notify=False)

    def save_templates(self):
        self.sync_templates_from_ui(notify=True)

    def set_templates_controls_state(self, enabled):
        state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        self.template1_entry.configure(state=state)
        self.template2_entry.configure(state=state)
        self.template_selector.configure(state=combo_state)
        self.save_templates_button.configure(state=state)

    def get_active_template_text(self):
        index = 0 if self.active_template == 1 else 1
        return self.templates[index]

    def format_elapsed(self, total_seconds: int) -> str:
        hours, remainder = divmod(max(0, int(total_seconds)), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    def get_elapsed_seconds(self) -> int:
        if self.started_at is None:
            return 0
        return max(0, int(time.monotonic() - self.started_at))

    def set_timer_label(self, elapsed_seconds: int):
        self.timer_var.set(f"Tiempo total: {self.format_elapsed(elapsed_seconds)}")

    def start_timer(self):
        self.started_at = time.monotonic()
        self.set_timer_label(0)
        self.schedule_timer_update()

    def schedule_timer_update(self):
        if not self.running or self.started_at is None:
            self.timer_after_id = None
            return
        self.set_timer_label(self.get_elapsed_seconds())
        self.timer_after_id = self.root.after(250, self.schedule_timer_update)

    def stop_timer(self) -> int:
        elapsed_seconds = self.get_elapsed_seconds()
        if self.timer_after_id is not None:
            self.root.after_cancel(self.timer_after_id)
            self.timer_after_id = None
        self.set_timer_label(elapsed_seconds)
        self.started_at = None
        return elapsed_seconds

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text="Conversor 1.1x y 1.2x", font=("Segoe UI", 15, "bold"))
        title.pack(anchor="w")

        ttk.Label(frame, textvariable=self.source_var).pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, textvariable=self.dest_var).pack(anchor="w")

        template_frame = ttk.LabelFrame(frame, text="Plantillas", padding=10)
        template_frame.pack(fill="x", pady=(10, 8))

        ttk.Label(template_frame, text="Plantilla 1 (cuentas secundarias)").grid(row=0, column=0, sticky="w")
        self.template1_entry = ttk.Entry(template_frame, textvariable=self.template1_var)
        self.template1_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))

        ttk.Label(template_frame, text="Plantilla 2 (cuentas primarias)").grid(row=1, column=0, sticky="w")
        self.template2_entry = ttk.Entry(template_frame, textvariable=self.template2_var)
        self.template2_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))

        ttk.Label(template_frame, text="Activa").grid(row=2, column=0, sticky="w")
        template_actions = ttk.Frame(template_frame)
        template_actions.grid(row=2, column=1, sticky="ew", padx=(8, 0))

        self.template_selector = ttk.Combobox(
            template_actions,
            state="readonly",
            values=["Plantilla 1", "Plantilla 2"],
            width=14,
        )
        self.template_selector.pack(side="left")
        self.template_selector.current(0 if self.active_template == 1 else 1)
        self.template_selector.bind("<<ComboboxSelected>>", self.on_template_selected)

        self.save_templates_button = ttk.Button(template_actions, text="Guardar plantillas", command=self.save_templates)
        self.save_templates_button.pack(side="right")

        template_frame.columnconfigure(1, weight=1)
        template_actions.columnconfigure(0, weight=1)

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(12, 8))

        self.start_button = ttk.Button(controls, text="Iniciar", command=self.start)
        self.start_button.pack(side="left")

        self.timer_label = ttk.Label(controls, textvariable=self.timer_var, font=("Segoe UI", 10, "bold"))
        self.timer_label.pack(side="right", padx=(0, 12))

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
        if not self.sync_templates_from_ui(notify=False):
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
        active_template_text = self.get_active_template_text()
        for video in videos:
            for folder_name, speed in TARGETS:
                jobs.append((video, folder_name, speed, active_template_text))

        self.total_jobs = len(jobs)
        self.done_jobs = 0
        self.ok_jobs = 0
        self.fail_jobs = 0
        self.running = True

        self.progress.configure(maximum=self.total_jobs, value=0)
        self.counter_var.set(f"0/{self.total_jobs}")
        self.status_var.set(f"Procesando {len(videos)} video(s) con {MAX_WORKERS} proceso(s) en paralelo")
        self.start_button.configure(state="disabled")
        self.set_templates_controls_state(False)
        self.start_timer()
        self.clear_log()
        self.append_log(f"Videos detectados: {len(videos)}")
        self.append_log(f"Tareas totales: {self.total_jobs}")
        self.append_log(f"Plantilla activa: {self.active_template}")

        worker = threading.Thread(target=self.worker, args=(jobs,), daemon=True)
        worker.start()

    def convert_one(self, job):
        video, folder_name, speed, suffix = job
        out_dir = self.output_root / folder_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = output_name_for(video, suffix)
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
                elapsed_seconds = self.stop_timer()
                elapsed_text = self.format_elapsed(elapsed_seconds)
                self.start_button.configure(state="normal")
                self.set_templates_controls_state(True)
                self.status_var.set("Conversion terminada")
                self.append_log(f"Finalizado. OK: {self.ok_jobs} | Errores: {self.fail_jobs}")
                self.append_log(f"Tiempo total: {elapsed_text}")
                if self.fail_jobs == 0:
                    messagebox.showinfo("Listo", f"Proceso completado sin errores.\nTiempo total: {elapsed_text}")
                else:
                    messagebox.showwarning(
                        "Finalizado",
                        f"Proceso completado con {self.fail_jobs} error(es).\nTiempo total: {elapsed_text}",
                    )

        self.root.after(120, self.on_event)

    def close(self):
        if self.running:
            leave = messagebox.askyesno("Salir", "Hay conversiones en curso. Deseas salir?")
            if not leave:
                return
        if self.timer_after_id is not None:
            self.root.after_cancel(self.timer_after_id)
            self.timer_after_id = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ConversorApp().run()
