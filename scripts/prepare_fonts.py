"""Prepare app-private font families from the downloaded source files.

Build dependency: fonttools. Runtime does not require fonttools.
"""
from pathlib import Path
import hashlib
import json
import tempfile

from fontTools import subset
from fontTools.merge import Merger
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables import ttProgram


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".runtime-deps" / "font-sources"
OUTPUT = ROOT / "assets" / "fonts"


def rename(font, family, bold=False):
    style = "Bold" if bold else "Regular"
    names = font["name"]
    # Remove old localized and typographic family names to avoid system collisions.
    names.names = [n for n in names.names if n.nameID not in (1, 2, 3, 4, 6, 16, 17)]
    for name_id, text in {1: family, 2: style, 3: f"{family}-{style}",
                          4: f"{family} {style}", 6: f"{family.replace(' ', '')}-{style}"}.items():
        names.setName(text, name_id, 3, 1, 0x409)
    return font


def unhint_thin_glyphs(font):
    # Times' hinting draws regular "z" with a 1px diagonal at 10-12pt, lighter
    # than neighbouring letters. Without instructions it renders smoothed.
    glyph = font["glyf"][font.getBestCmap()[ord("z")]]
    glyph.program = ttProgram.Program()
    glyph.program.fromBytecode(b"")
    return font


def prepare():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for source, family, output, bold in (
        ("msyh.ttc", "ZNote YaHei", "yahei.ttf", False),
        ("msyhbd.ttc", "ZNote YaHei", "yahei-bold.ttf", True),
        ("simsun.ttc", "ZNote SimSun", "simsun.ttf", False),
    ):
        font = TTFont(SOURCE / source, fontNumber=0)
        rename(font, family, bold).save(OUTPUT / output)
        font.close()

    # A single family with both scripts works in Text, Canvas and title Entry.
    # It prevents Tk's machine-dependent fallback from choosing another CJK font.
    for source, output, bold in (("times.ttf", "classic.ttf", False),
                                 ("timesbd.ttf", "classic-bold.ttf", True)):
        latin = TTFont(SOURCE / source)
        chinese = TTFont(SOURCE / "simsun.ttc", fontNumber=0)
        options = subset.Options()
        options.layout_features = []
        sub = subset.Subsetter(options=options)
        sub.populate(unicodes=set(chinese.getBestCmap()) - set(latin.getBestCmap()))
        sub.subset(chinese)
        scale_upem(chinese, latin["head"].unitsPerEm)
        # These Windows-only layout/bitmap tables are not needed for the CJK
        # glyphs. Keep Times' Latin kerning and use its metrics for line height.
        for font in (latin, chinese):
            for table in ("GSUB", "GPOS", "GDEF", "BASE", "JSTF", "DSIG", "FFTM", "LTSH", "VDMX", "hdmx", "EBLC", "EBDT", "EBSC", "vhea", "vmtx"):
                if table in font:
                    del font[table]
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime-deps") as directory:
            paths = [str(Path(directory) / name) for name in ("latin.ttf", "chinese.ttf")]
            latin.save(paths[0])
            chinese.save(paths[1])
            font = Merger().merge(paths)
        # Preserve original Times line spacing rather than merge's maximum.
        for attr in ("ascent", "descent", "lineGap"):
            setattr(font["hhea"], attr, getattr(latin["hhea"], attr))
        for attr in ("sTypoAscender", "sTypoDescender", "sTypoLineGap", "usWinAscent", "usWinDescent"):
            setattr(font["OS/2"], attr, getattr(latin["OS/2"], attr))
        if not bold:
            unhint_thin_glyphs(font)
        rename(font, "ZNote Classic", bold).save(OUTPUT / output)
        font.close()
        latin.close()
        chinese.close()

    manifest = {"repository": "https://github.com/wachin/Microsoft-Fonts",
                "commit": (SOURCE / "source-commit.txt").read_text().strip(),
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(SOURCE.iterdir()) if p.suffix in (".ttf", ".ttc")},
                "bundled_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in sorted(OUTPUT.glob("*.ttf"))}}
    (OUTPUT / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    prepare()
