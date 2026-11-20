"""A stand-in for the `piper` CLI: reads text on stdin, writes a short WAV to `-f`.

Exits 3 when the text contains FAIL, so the failure path can be tested.
"""

import struct
import sys
import wave

args = sys.argv[1:]
output = args[args.index("-f") + 1]
text = sys.stdin.read()
if "FAIL" in text:
    sys.exit(3)
scale = float(args[args.index("--length-scale") + 1])
with wave.open(output, "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(22050)
    frames = int(22050 * 0.05 * max(1, len(text) // 10) * scale)
    audio.writeframes(b"".join(struct.pack("<h", 0) for _ in range(frames)))
