PRIMARY_MODEL = "llama-3.3-70b-versatile"
# FALLBACK_MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS = 8192
TEMPERATURE = 0.2
MAX_FILE_SIZE_MB = 5
ALLOWED_EXTENSIONS = [".txt", ".md", ".py", ".json", ".csv", ".yaml"]

MAX_FILE_SIZE_BYTES = int(MAX_FILE_SIZE_MB * 1024 * 1024)
DESTRUCTIVE_TOOLS = {"write_file", "delete_file", "move_file"}
