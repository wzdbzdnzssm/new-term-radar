"""Single state root shared by scan, template discovery, review and receipts."""
import os
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get('AI_MONITOR_ROOT', str(Path.home() / '.hermes/data/ai-video-monitor')))
