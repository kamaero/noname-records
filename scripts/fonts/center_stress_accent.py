"""Сдвинуть знак ударения U+0301 в IBM Plex Sans влево и привязать его к э/ю/я.

У Plex якорь знака стоит правее середины буквы (так он рисует латинское «á»), и в
Читалке ударение читалось между букв: «изги́б» выглядело как «изги'б». У э, ю, я якоря
нет вовсе. Сдвиг выбран глазами владельца на сравнении вариантов (2026-09-29):
+70 единиц якоря знака = знак на 0,07 em левее, ровно над серединой «и», «а», «е».

Шрифт переименовывается в «Noname Sans» (OFL: имя «Plex» зарезервировано за
неизменённым шрифтом).

Запускать на ИСХОДНОМ файле с Google Fonts, а не на уже поправленном — сдвиг сложится:
    python3 scripts/fonts/center_stress_accent.py исходный.woff2 frontend/src/assets/fonts/ibm-plex-sans-cyrillic.woff2 70
Нужен fontTools с brotli (системный python3).
"""
import copy, sys
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib.tables import otTables as ot

def patch(src, dst, shift):
    f = TTFont(src); cmap = f.getBestCmap(); gs = f.getGlyphSet()
    def cx(g):
        p = BoundsPen(gs); gs[g].draw(p); b = p.bounds; return (b[0] + b[2]) / 2, b[3]
    acute = cmap[0x301]
    lk = f['GPOS'].table.LookupList.Lookup
    for L in lk:
        for st in L.SubTable:
            t = st.ExtSubTable if L.LookupType == 9 else st
            if t.__class__.__name__ != 'MarkBasePos' or acute not in t.MarkCoverage.glyphs:
                continue
            mrec = t.MarkArray.MarkRecord[t.MarkCoverage.glyphs.index(acute)]
            # Знак рисуется так, что его якорь совпадает с якорем буквы: сдвинуть якорь
            # знака вправо — значит сдвинуть сам знак влево над каждой буквой сразу.
            mrec.MarkAnchor.XCoordinate += shift
            cls = mrec.Class
            # Гласные без якоря: э ю я и заглавные — якорь по центру буквы, на высоте «о».
            ref = cmap[0x43E]
            refrec = t.BaseArray.BaseRecord[t.BaseCoverage.glyphs.index(ref)].BaseAnchor[cls]
            refrecU = t.BaseArray.BaseRecord[t.BaseCoverage.glyphs.index(cmap[0x41E])].BaseAnchor[cls]
            glyphs = list(t.BaseCoverage.glyphs); records = list(t.BaseArray.BaseRecord)
            added = []
            for cp in (0x44D, 0x44E, 0x44F, 0x42D, 0x42E, 0x42F):
                g = cmap.get(cp)
                if g is None or g in glyphs: continue
                gc, _top = cx(g); upper = cp < 0x430
                src_anchor = refrecU if upper else refrec
                a = copy.deepcopy(src_anchor)
                # сдвиг якоря «о» относительно центра «о» переносим на новую букву
                ocx, _ = cx(cmap[0x41E] if upper else ref)
                a.XCoordinate = round(gc + (src_anchor.XCoordinate - ocx))
                rec = ot.BaseRecord(); rec.BaseAnchor = [None] * t.ClassCount; rec.BaseAnchor[cls] = a
                glyphs.append(g); records.append(rec); added.append(chr(cp))
            order = sorted(range(len(glyphs)), key=lambda i: f.getGlyphID(glyphs[i]))
            t.BaseCoverage.glyphs = [glyphs[i] for i in order]
            t.BaseArray.BaseRecord = [records[i] for i in order]
            t.BaseArray.BaseCount = len(order)
    # OFL IBM Plex резервирует имя «Plex»: изменённую версию нельзя распространять под
    # ним, а сайт раздаёт файл каждому посетителю. Лицензия и копирайт IBM остаются.
    for rec in f['name'].names:
        if rec.nameID in (0, 13, 14):
            continue
        value = rec.toUnicode()
        renamed = value.replace("IBM Plex Sans", "Noname Sans").replace("IBMPlexSans", "NonameSans")
        if renamed != value:
            rec.string = renamed
    f.flavor = 'woff2'; f.save(dst)

if __name__ == '__main__':
    patch(sys.argv[1], sys.argv[2], int(sys.argv[3]))
