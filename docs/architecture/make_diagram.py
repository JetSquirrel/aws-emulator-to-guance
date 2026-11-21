"""Generate the lab architecture diagram (SVG, then PNG via headless Chrome).

    python3 docs/architecture/make_diagram.py [aws-icons-dir]

Icons default to ./icons (the few this diagram uses, copied from the `aws-icons` npm
package, MIT, which repackages the official AWS Architecture Icons). Needs Google
Chrome for the PNG. Outputs architecture.svg/.png next to this script.
"""

import base64
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ICONS = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "icons"
W, H = 1600, 960

C = {
    "bg0": "#0d1424", "bg1": "#191431", "text": "#f3f4f6", "muted": "#9aa3b5",
    "aws": "#ff9900", "app": "#4c8dff", "dk": "#a78bfa", "guance": "#38bdf8",
    "req": "#ff9900", "msg": "#ff5fa2", "tel": "#a78bfa", "frame": "#5b6478",
}
SANS = "'PingFang SC','Hiragino Sans GB','Helvetica Neue',Arial,sans-serif"
MONO = "'SF Mono',Menlo,Consolas,monospace"


def icon(rel: str, x: float, y: float, size: float, light: bool = False) -> str:
    """Inline an icon SVG as a nested <svg>; recolor dark line icons for the dark theme."""
    src = (ICONS / f"{rel}.svg").read_text()
    src = re.sub(r"<title>.*?</title>", "", src)
    if light:
        src = re.sub(r'fill="#232f3[de]"', 'fill="#e5e7eb"', src)
    vb = re.search(r'viewBox="([^"]+)"', src).group(1)
    inner = src[src.index(">") + 1: src.rindex("</svg>")]
    return f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="{vb}">{inner}</svg>'


def text(x, y, s, size=16, color=None, weight=400, anchor="middle", font=SANS, spacing=0):
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color or C["text"]}" text-anchor="{anchor}" letter-spacing="{spacing}">{s}</text>')


def panel(x, y, w, h, color, title, sub="", dashed=False, fill_alpha="0f"):
    dash = ' stroke-dasharray="8 7"' if dashed else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{color}{fill_alpha}" '
           f'stroke="{color}" stroke-opacity="0.8" stroke-width="2"{dash}/>']
    if title:
        out.append(text(x + 22, y + 36, title, 15, color, 700, "start", MONO, 2))
    if sub:
        out.append(text(x + 22, y + 58, sub, 13, C["muted"], 400, "start"))
    return "\n".join(out)


def node(cx, cy, rel, name, sub, light=False, size=64):
    return "\n".join([
        icon(rel, cx - size / 2, cy - size / 2, size, light),
        text(cx, cy + size / 2 + 26, name, 17, weight=700),
        text(cx, cy + size / 2 + 48, sub, 13, C["muted"]),
    ])


def pill(x, y, s, color, size=12):
    w = 9 * len(s.encode("gbk", "ignore")) * size / 14 + 22
    return (f'<rect x="{x - w / 2}" y="{y - 12}" width="{w}" height="24" rx="12" fill="{C["bg0"]}" '
            f'stroke="{color}" stroke-width="1.5"/>' + text(x, y + 4.5, s, size, color, 500, font=MONO))


def edge(d, color, dashed=False, marker=True):
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    m = f' marker-end="url(#arr-{color[1:]})"' if marker else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.5"{dash}{m}/>'


def marker(color):
    return (f'<marker id="arr-{color[1:]}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{color}"/></marker>')


parts = []
# ---- background, title ----
parts.append(f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C["bg0"]}"/>'
             f'<stop offset="1" stop-color="{C["bg1"]}"/></linearGradient>'
             + "".join(marker(c) for c in (C["req"], C["msg"], C["tel"])) + "</defs>")
parts.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
parts.append(text(40, 62, "订单实验室：AWS Emulator × 观测云", 34, weight=800, anchor="start"))
parts.append(f'<rect x="40" y="76" width="110" height="5" rx="2.5" fill="{C["aws"]}"/>')

# ---- laptop frame ----
parts.append(panel(30, 100, 1160, 770, C["frame"], "", dashed=True, fill_alpha="00"))
parts.append(text(54, 130, "我的电脑", 15, C["muted"], anchor="start", font=MONO))

# ---- application services (left) ----
parts.append(panel(60, 150, 270, 600, C["app"], "应用服务"))
parts.append(node(195, 300, "resource/GenericApplication", "order-api", "下单", light=True))
parts.append(node(195, 560, "resource/GenericApplication", "pricing-service", "定价", light=True))

# ---- AWS Emulator (middle) ----
parts.append(panel(380, 150, 480, 600, C["aws"], "", fill_alpha="10"))
parts.append(icon("architecture-group/AWSCloudDark", 400, 166, 34))
parts.append(text(446, 190, "AWS Emulator", 22, C["aws"], 800, "start"))
parts.append(node(620, 300, "architecture-service/AmazonSimpleStorageService", "Amazon S3", ""))
parts.append(node(620, 470, "architecture-service/AmazonDynamoDB", "Amazon DynamoDB", ""))
parts.append(node(620, 640, "architecture-service/AmazonSimpleQueueService", "Amazon SQS", ""))

# ---- shipping worker (right) ----
parts.append(panel(910, 150, 250, 600, C["app"], "应用服务"))
parts.append(node(1035, 560, "resource/GenericApplication", "shipping-worker", "发货", light=True))

# ---- DataKit ----
parts.append(f'<rect x="910" y="778" width="250" height="68" rx="14" fill="{C["dk"]}1f" stroke="{C["dk"]}" stroke-width="2"/>')
parts.append(text(1035, 806, "DataKit", 19, weight=800))
parts.append(text(1035, 830, "OpenTelemetry 采集器", 13, C["muted"]))

# ---- Guance (right, outside the laptop) ----
parts.append(panel(1220, 100, 350, 770, C["guance"], "", fill_alpha="12"))
# Guance logo (dark-background variant, from guance.com/images/brand/guance-cn-dark.webp), 384x96
logo = base64.b64encode((HERE / "guance-cn-dark.png").read_bytes()).decode()
parts.append(f'<image x="1244" y="118" width="192" height="48" href="data:image/png;base64,{logo}"/>')
cards = ["链路追踪", "服务拓扑", "指标与仪表板", "日志", "AI 根因分析"]
for i, title in enumerate(cards):
    y = 215 + i * 122
    parts.append(f'<rect x="1246" y="{y}" width="298" height="92" rx="12" fill="#0d1424" fill-opacity="0.55" '
                 f'stroke="{C["guance"]}" stroke-opacity="0.45" stroke-width="1.5"/>')
    parts.append(f'<rect x="1246" y="{y + 22}" width="4" height="48" rx="2" fill="{C["guance"]}"/>')
    parts.append(text(1274, y + 54, title, 21, weight=700, anchor="start"))

# ---- request path (orange) ----
parts.append(edge("M195 395 L195 516", C["req"]))
parts.append(pill(195, 455, "HTTP", C["req"]))
for ty in (300, 470, 640):
    parts.append(edge(f"M232 300 C 420 300, 420 {ty}, 578 {ty}", C["req"]))
parts.append(pill(470, 258, "AWS SDK", C["req"]))
for ty in (300, 470):
    parts.append(edge(f"M995 560 C 820 560, 820 {ty}, 662 {ty}", C["req"]))

# ---- messaging (pink) ----
parts.append(edge("M662 640 C 820 640, 860 580, 993 572", C["msg"]))

# ---- telemetry (dashed purple) ----
parts.append(edge("M155 300 L88 300 L88 812 L906 812", C["tel"], dashed=True))
parts.append(edge("M155 560 L88 560", C["tel"], dashed=True, marker=False))
parts.append(edge("M1035 660 L1035 774", C["tel"], dashed=True))
parts.append(pill(520, 812, "OTLP", C["tel"]))
parts.append(edge("M1160 812 L1216 812", C["tel"], dashed=True))

# ---- legend + attribution ----
lx = 40
for color, label, dashed in ((C["req"], "请求路径", False), (C["msg"], "消息", False),
                             (C["tel"], "遥测数据", True)):
    parts.append(edge(f"M{lx} 915 L{lx + 44} 915", color, dashed, marker=False))
    parts.append(text(lx + 56, 920, label, 15, C["muted"], anchor="start"))
    lx += 92 + 16 * len(label)
parts.append(text(1570, 920, "AWS Architecture Icons © Amazon Web Services", 13, "#6b7280", anchor="end"))

svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n' + "\n".join(parts) + "\n</svg>\n"
(HERE / "architecture.svg").write_text(svg)

html = HERE / "_render.html"
html.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>html,body{{margin:0;background:#0d1424}}</style>'
                f'</head><body>{svg}</body></html>')
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                f"--window-size={W},{H}", f"--screenshot={HERE / 'architecture.png'}", html.as_uri()],
               check=True, capture_output=True)
html.unlink()
print("wrote", HERE / "architecture.svg", "and", HERE / "architecture.png")
