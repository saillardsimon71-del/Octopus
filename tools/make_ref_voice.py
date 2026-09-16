import asyncio
import subprocess
import sys
from pathlib import Path

import edge_tts

TEXT = ("Bonjour et bienvenue. Je vais vous présenter quelques phrases, "
        "prononcées de façon claire et naturelle, à un rythme posé. "
        "Cette voix est là pour vous accompagner au quotidien, "
        "avec simplicité et chaleur humaine. Merci de votre écoute.")

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("ref_fr.mp3")
VOICE = sys.argv[2] if len(sys.argv) > 2 else "fr-FR-VivienneNeural"


async def main():
    c = edge_tts.Communicate(TEXT, VOICE, rate="+0%")
    await c.save(str(OUT))
    print("saved", OUT)


asyncio.run(main())
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(OUT),
                "-ar", "44100", "-ac", "1", str(OUT.with_suffix(".wav"))])
print("wav", OUT.with_suffix(".wav"))
