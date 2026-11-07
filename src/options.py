"""The options the admin page can change. Each can still start from its environment variable; a saved value wins."""

from settings import Setting

DEFAULT_MODEL = "hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M"

SETTINGS = [
    Setting("update_check_hours", "Check for updates every (hours)", "float", 6.0, "SERVICE_UPDATE_CHECK_HOURS", 0, 720,
            help="0 = only when you press Check now"),
    Setting("max_inflight", "Calls allowed to wait on Ollama", "int", 0, "SERVICE_MAX_INFLIGHT", 0, 10000,
            help="Per replica; more get 429. 0 = 4 x Ollama's parallel requests"),
    Setting("cache_size", "Answers kept per replica", "int", 256, "SERVICE_CACHE_SIZE", 0, 100000,
            help="The last answers for the same request, sent again without asking Ollama. Only temperature 0 requests are kept. 0 = off"),
    Setting("project_prefix", "Project model prefix", "text", "minicpm-", "SERVICE_PROJECT_PREFIX",
            help="A project uses the Ollama model named prefix + project"),
    Setting("model", "Base model", "text", DEFAULT_MODEL, "SERVICE_OLLAMA_MODEL", restart=True, blank=False,
            help="After a restart, pull a new one from the Updates card"),
    Setting("warm", "Load the model at start", "bool", True, "SERVICE_WARM", restart=True),
]
