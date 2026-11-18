from pathlib import Path
import server

for path in (
    server.DB_PATH,
    Path(str(server.DB_PATH) + "-wal"),
    Path(str(server.DB_PATH) + "-shm"),
):
    if path.exists():
        path.unlink()
server.init_db()
print(f"Reset {server.DB_PATH.name}. ResourceBridge is empty again.")
