#!/usr/bin/env python3
"""
prepare_historie2.py -- прежние редакции из снимков Архива интернета (collect_wayback_gii.py) + объединение с уже загруженными (05.10.2026).

Наблюдения: (закон, §, дата снимка, текст). Для каждого параграфа -- цепочка различающихся редакций: «видели с … до …; изменена до …».
  1) редакции, которых ещё нет в базе (ни среди действующих, ни среди laws_historie) -> de_law_corpus/prepared/laws_historie2.jsonl
     (источник тот же: laws_historie; загрузчик: --sources laws_historie2 не нужен -- файл подаётся под именем источника laws_historie2,
      в документах стоит source = laws_historie);
  2) у уже загруженных редакций уточняются окна (valid_from раньше / valid_until позже / changed_before раньше) -- UPDATE без пересчёта векторов.
  python3 prepare_historie2.py [--apply]     (без --apply окна не меняются, только отчёт)
"""
import os
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import psycopg2
from psycopg2.extras import Json

W = Path("quellen_neu/wayback")
OUT = Path("de_law_corpus/prepared/laws_historie2.jsonl")


def norm(t):
    return re.sub(r"[^0-9a-zäöüß§]", "", t.lower())


def h(t):
    return hashlib.md5(norm(t).encode()).hexdigest()


def bag(t):
    """Отпечаток без учёта порядка слов: разные разборы вложенных списков переставляют куски текста, редакция при этом та же."""
    return hashlib.md5(" ".join(sorted(re.findall(r"[0-9a-zäöüß§]+", t.lower()))).encode()).hexdigest()


def text_of(el):
    """Текст Content в порядке документа: абзацы <P> и элементы списков -- с новой строки (рекурсивно: хвост элемента -- после его детей)."""
    parts = []

    def walk(node):
        if node.tag in ("P", "LA", "DT", "DD", "entry", "Title", "BR"):
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for ch in node:
            walk(ch)
            if ch.tail:
                parts.append(ch.tail)

    walk(el)
    t = "".join(parts)
    t = "\n".join(" ".join(l.split()) for l in t.splitlines())
    return re.sub(r"\n{2,}", "\n", t).strip()


def parse_zip(path):
    """-> [(jurabk, '§ 32a', Titel, Text)]"""
    out = []
    try:
        z = zipfile.ZipFile(path)
        name = next(n for n in z.namelist() if n.endswith(".xml"))
        root = ET.fromstring(z.read(name))
    except Exception:
        return out
    for n in root.iter("norm"):
        md = n.find("metadaten")
        if md is None:
            continue
        enbez = (md.findtext("enbez") or "").strip()
        abk = (md.findtext("jurabk") or "").strip()
        m = re.match(r"^(§+|Art\.?|Artikel) ?(\d+[a-z]*)$", enbez)
        c = n.find("textdaten/text/Content")
        if not (m and abk and c is not None):
            continue
        txt = text_of(c)
        if len(txt) >= 200:                      # короче загрузчик не берёт
            num = ("Art" if m.group(1).startswith("Art") else m.group(1)) + " " + m.group(2)
            tt = md.find("titel")
            out.append((abk, num, " ".join("".join(tt.itertext()).split()) if tt is not None else "", txt))
    return out


def main():
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(dbname="steuer_corpus_test", user=os.environ.get("PGUSER", "postgres"), password=os.environ.get("PGPASSWORD"), host=os.environ.get("PGHOST", "localhost"))
    cur = conn.cursor()
    cur.execute("SELECT id, source, title, full_text, valid_from, valid_until, metadata->>'changed_before' FROM documents WHERE source IN ('laws','laws_historie')")
    current, hist, hist_bag = defaultdict(set), {}, {}
    for i, src, title, text, vf, vu, cb in cur.fetchall():
        m = re.match(r"^(.*?) ((?:§+|Art) \d+[a-z]*)\b", title)
        if not m:
            continue
        key = (m.group(1), m.group(2))
        if src == "laws":
            current[key].add(h(text))
            current[key].add(bag(text))
        else:
            hist[(key, h(text))] = (i, str(vf) if vf else None, str(vu) if vu else None, cb, title)
            hist_bag[(key, bag(text))] = h(text)
    print(f"в базе: действующих параграфов {len(current)}, прежних редакций {len(hist)}", flush=True)
    obs = defaultdict(dict)        # (abk, §) -> {дата: (хеш, заголовок, текст)}
    n_zip = 0
    for d in sorted(p for p in W.iterdir() if p.is_dir()):
        for f in sorted(d.glob("*.zip")):
            date = f"{f.stem[:4]}-{f.stem[4:6]}-{f.stem[6:8]}"
            for abk, num, head, txt in parse_zip(f):
                hh = h(txt)
                bb = bag(txt)
                if ((abk, num), hh) not in hist and ((abk, num), bb) in hist_bag:
                    hh = hist_bag[((abk, num), bb)]              # та же редакция, что уже загружена, в другом порядке кусков
                elif bb in current.get((abk, num), ()):
                    hh = "CURRENT"
                obs[(abk, num)][date] = (hh, head, txt)
            n_zip += 1
    print(f"снимков разобрано: {n_zip}; параграфов с наблюдениями: {len(obs)}", flush=True)
    new_docs, upd = [], []
    seen_ids, n_wieder = set(), 0
    for key, by_date in obs.items():
        dates = sorted(by_date)
        runs = []                                   # [хеш, первая дата, последняя дата, заголовок, текст, изменена до]
        for dte in dates:
            hh, head, txt = by_date[dte]
            if runs and runs[-1][0] == hh:
                runs[-1][2] = dte
            else:
                if runs:
                    runs[-1][5] = dte
                runs.append([hh, dte, dte, head, txt, None])
        for hh, first, last, head, txt, changed in runs:
            if hh == "CURRENT" or hh in current.get(key, ()):
                continue                            # действующая редакция
            if (key, hh) in hist:                   # уже загружена -- уточнить окно
                i, vf, vu, cb, title = hist[(key, hh)]
                nf, nu = min(x for x in (vf, first) if x), max(x for x in (vu, last) if x)
                nc = min([x for x in (cb, changed) if x], default=None)
                if (nf, nu, nc) != (vf, vu, cb):
                    upd.append((i, nf, nu, nc, key, head))
                continue
            span = f"gesehen {first}" + (f" bis {last}" if last != first else "") + (f"; geändert vor {changed}" if changed else "; geändert nach diesem Stand")
            did = f"hist_{re.sub(r'[^A-Za-z0-9]+', '_', key[0])}_{re.sub(r'[^A-Za-z0-9]+', '', key[1])}_h{hh[:10]}"
            if did in seen_ids:                       # тот же текст появился снова после другой редакции (А -> Б -> А): второй документ не нужен
                n_wieder += 1
                continue
            seen_ids.add(did)
            new_docs.append({"doc_id": did,
                             "source": "laws_historie", "title": f"{key[0]} {key[1]} - {head}".rstrip(" -") + f" (frühere Fassung, {span})",
                             "text": txt, "tier": 1, "origin": "https://web.archive.org/ (gesetze-im-internet.de, xml.zip)", "law_state_year": int(last[:4]),
                             "valid_from": first, "valid_until": last, "changed_before": changed})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for d in new_docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"новых редакций: {len(new_docs)} -> {OUT}; уточнений окон у загруженных: {len(upd)}; повторов прежнего текста пропущено: {n_wieder}", flush=True)
    if apply:
        for i, nf, nu, nc, key, head in upd:
            span = f"gesehen {nf}" + (f" bis {nu}" if nu != nf else "") + (f"; geändert vor {nc}" if nc else "; geändert nach diesem Stand")
            title = f"{key[0]} {key[1]} - {head}".rstrip(" -") + f" (frühere Fassung, {span})"
            cur.execute("""UPDATE documents SET valid_from = %s, valid_until = %s, title = %s,
                                  metadata = metadata || %s WHERE id = %s""", (nf, nu, title[:500], Json({"changed_before": nc}), i))
            cur.execute("UPDATE document_titles SET title_text = %s WHERE document_id = %s", (title, i))
        conn.commit()
        print(f"окна обновлены: {len(upd)}", flush=True)


if __name__ == "__main__":
    main()
