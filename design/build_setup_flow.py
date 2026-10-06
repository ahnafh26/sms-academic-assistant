"""Build design/setup-flow.html from setup-flow.src.html by embedding logos as data URIs.

The published page can't load external images (artifact CSP), so every
logo in design/logos/<slug>.png is inlined. Schools without a logo file
fall back to the monogram defined in the page.
"""

import base64
import json
from pathlib import Path

HERE = Path(__file__).parent
logos = {
    p.stem: "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    for p in sorted((HERE / "logos").glob("*.png"))
}
src = (HERE / "setup-flow.src.html").read_text(encoding="utf-8")
assert "__LOGOS__" in src
out = src.replace("__LOGOS__", json.dumps(logos, indent=1))
(HERE / "setup-flow.html").write_text(out, encoding="utf-8")
print(f"embedded {len(logos)} logos ({', '.join(logos)}); {len(out) // 1024} KB")
