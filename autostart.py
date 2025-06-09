import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time

class RestartOnChangeHandler(FileSystemEventHandler):
    def __init__(self, script_path):
        self.script_path = script_path
        self.process = None
        self.start_bot()

    def start_bot(self):
        print("▶️ Запуск бота...")
        self.process = subprocess.Popen(["python", self.script_path])

    def stop_bot(self):
        if self.process:
            print("⏹️ Перезапуск бота...")
            self.process.terminate()
            self.process.wait()

    def on_modified(self, event):
        if event.src_path.endswith("bot.py"):
            self.stop_bot()
            self.start_bot()

if __name__ == "__main__":
    script = "bot.py"
    event_handler = RestartOnChangeHandler(script)
    observer = Observer()
    observer.schedule(event_handler, path=".", recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()