"""Shared design tokens for every RetroGuide web surface (desktop HUD and the
ROAMER mobile PWA).

This is the single source of truth for era colour/type/texture values: both
``web.py`` and ``mobile.py`` inject ``TOKENS_CSS`` verbatim so the two pages
can never drift apart. Never hardcode a colour, font or scanline strength in a
page — add or change a token here instead.
"""
from __future__ import annotations

TOKENS_CSS = r"""
:root{
  /* 90s cable is the default era */
  --bg:#070b12; --panel:#0e1521; --panel2:#141d2c; --border:#21304a;
  --text:#e8edf5; --muted:#8794a8; --accent:#36e0c8; --now:#ff5e8a;
  --font:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;
  --scan:.16;        /* scanline strength */
  --bloom:rgba(54,224,200,.08);
  /* text/icons drawn directly on top of --accent */
  --on-accent:#06121a;
  /* translucent wash behind the ON AIR row/now accents */
  --now-wash:rgba(255,94,138,.07);
}
/* 70s: warm amber, woodgrain dark, serif, heavy glass */
html[data-era="70s"]{
  --bg:#140d06; --panel:#1d140a; --panel2:#271a0d; --border:#3a2a14;
  --text:#f3e6d0; --muted:#b79a72; --accent:#e8a13a; --now:#df6a2c;
  --font:Georgia,"Times New Roman",serif; --scan:.26; --bloom:rgba(232,161,58,.10);
}
/* 80s: neon magenta/cyan on black, mono, glow */
html[data-era="80s"]{
  --bg:#06010f; --panel:#10071f; --panel2:#180b2c; --border:#34155a;
  --text:#f4ecff; --muted:#a98fce; --accent:#ff3df0; --now:#00e5ff;
  --font:"Courier New",ui-monospace,monospace; --scan:.22; --bloom:rgba(255,61,240,.12);
}
/* 00s: sleek digital-cable blue/silver, thin, sharp */
html[data-era="00s"]{
  --bg:#070b14; --panel:#0d1422; --panel2:#131c2e; --border:#243349;
  --text:#eef3fa; --muted:#8aa0bd; --accent:#4aa3ff; --now:#ff5e8a;
  --font:"Segoe UI",system-ui,Helvetica,Arial,sans-serif; --scan:.07; --bloom:rgba(74,163,255,.08);
}
"""
