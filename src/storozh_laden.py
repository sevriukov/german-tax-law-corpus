#!/usr/bin/env python3
"""Сторож загрузки (16.10.2026; 18.10.2026 -- параметры вместо жёсткой привязки к chain_quellen_2026_10l.sh, пороги GPU, лестница 64->48->32).
Правило (владелец проекта через Main, 18.10.2026): ни одна цепочка загрузки не стартует без сторожа.
Раз в 60 с:
  - цепочка завершилась без маркера, в журнале загрузки новый Traceback, или он молчит дольше --hang-min минут
    -> остановка по точным PID (цепочка, загрузчик, все его потомки), пауза, перезапуск той же цепочки
       (загрузчик пропускает записанные doc_id; запись в БД -- целыми пачками по 200 док. в одной транзакции, частичных нет);
  - сбои подряд без новых документов в БД -> батч по лестнице 64 -> 48 -> 32 (пишется в quellen_neu/emb_batch);
    как только документов прибавилось -- обратно 64 (правило владельца проекта 16.10: насовсем не снижать). 5 сбоев без прогресса -> стоп и запись;
  - GPU >= --temp-limit °C или память >= --mem-limit MiB (по rebuild_watchdog.py: 90 °C; 8193 = по памяти не останавливать) -> остановка,
    пауза 300 с, перезапуск;
  - маркер завершения в журнале цепочки -> выход. Журнал сторожа: quellen_neu/storozh_laden.log
    setsid python3 -u storozh_laden.py --chain ./chain_morgen_2026_10_18.sh --load-log quellen_neu/chain_morgen_18_load.log \
        --chain-log quellen_neu/chain_nacht_17.log --done CHAIN_MORGEN_18_DONE > /dev/null 2>&1 &"""
import argparse
import os
import re
import subprocess
import time
from datetime import datetime

os.chdir(".")
ap = argparse.ArgumentParser()
ap.add_argument("--chain", required=True)
ap.add_argument("--load-log", required=True)
ap.add_argument("--chain-log", required=True)
ap.add_argument("--done", required=True)
ap.add_argument("--hang-min", type=int, default=45)
ap.add_argument("--temp-limit", type=int, default=90)
ap.add_argument("--mem-limit", type=int, default=8193)
A = ap.parse_args()
# 23.10.2026 (Main, после ночного сбоя: два оркестра шли параллельно): одна цепочка на карты — второй сторож не стартует
import fcntl, sys as _sys
_lock = open("/tmp/leckermath_storozh.lock", "w")
try:
    fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("СТОП: уже идёт другая цепочка под сторожем (/tmp/leckermath_storozh.lock)", flush=True)
    _sys.exit(2)
EV, BATCH, LEITER, COOL = "quellen_neu/storozh_laden.log", "quellen_neu/emb_batch", ["64", "48", "32"], 300
CHAIN_NAME = os.path.basename(A.chain)


def ev(m):
    with open(EV, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%F %T} [{CHAIN_NAME}] {m}\n")


def fehler():
    try:
        return len(re.findall(rb"Traceback", open(A.load_log, "rb").read()))
    except FileNotFoundError:
        return 0


def docs():
    r = subprocess.run(["docker", "exec", "steuer_db", "psql", "-U", "postgres", "-d", "steuer_corpus_test", "-tAc", "select count(*) from documents"],
                       capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def gpus():
    out = subprocess.run(["nvidia-smi", "--query-gpu=index,temperature.gpu,memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return [tuple(int(x) for x in l.split(",")) for l in out.strip().splitlines()]


def pids():
    out = subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout.splitlines()
    rows = [(int(p), int(pp), a) for p, pp, a in (l.split(None, 2) for l in out[1:] if len(l.split(None, 2)) == 3)]
    chain = [p for p, pp, a in rows if CHAIN_NAME in a and "storozh" not in a and a.startswith("/bin/bash")]
    nachfahren, front = [], list(chain)
    while front:
        kids = [p for p, pp, a in rows if pp in front]
        nachfahren += kids
        front = kids
    return chain, nachfahren


def stopp(grund):
    chain, nach = pids()
    ev(f"{grund}; останавливаю цепочку {chain}, потомки {nach}")
    for p in chain:
        subprocess.run(["kill", str(p)], stderr=subprocess.DEVNULL)
    time.sleep(1)
    for p in nach:
        subprocess.run(["kill", str(p)], stderr=subprocess.DEVNULL)
    time.sleep(15)
    for p in nach:
        subprocess.run(["kill", "-9", str(p)], stderr=subprocess.DEVNULL)
    time.sleep(20)


def start(grund, batch):
    open(BATCH, "w").write(batch + "\n")
    # 31.10.2026: журнал загрузки от прошлого прогона (KF2 29.10) был старше --hang-min -> «молчит 2606 мин» через 3 мин после старта, 5 ложных перезапусков;
    # отсчёт тишины — с момента запуска
    open(A.load_log, "a").close(); os.utime(A.load_log)
    with open(A.chain_log, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%F %T} сторож: {grund}; перезапуск {CHAIN_NAME}, EMB_BATCH {batch}\n")
    subprocess.Popen(["setsid", A.chain], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ev(f"запущена цепочка, EMB_BATCH {batch}")
    time.sleep(120)


def fehlerzeile():
    """последняя строка вида «XxxError: …» в журнале загрузки (итог последнего Traceback)"""
    try:
        z = [l.strip() for l in open(A.load_log, encoding="utf-8", errors="replace").read().splitlines()[-400:]
             if re.match(r"^[\w.]+(Error|Exception)\b", l.strip())]
    except FileNotFoundError:
        return None
    return z[-1] if z else None


def fertig():
    try:
        return A.done in open(A.chain_log, encoding="utf-8").read()
    except FileNotFoundError:
        return False


ev(f"сторож запущен: зависание {A.hang_min} мин, пороги {A.temp_limit} °C / {A.mem_limit} MiB, лестница {LEITER}")
stufe, n_err, d_fail, n_ohne = 0, fehler(), None, 0
if not pids()[0] and not fertig():
    start("старт сторожа", LEITER[0])
while True:
    time.sleep(60)
    if fertig():
        ev(f"{A.done} -- сторож выходит")
        if stufe:
            open(BATCH, "w").write(LEITER[0] + "\n")
        break
    d = docs()
    if stufe and d_fail is not None and d > d_fail:          # трудная пачка прошла на меньшем батче -> обратно 64
        stopp(f"трудная пачка записана на батче {LEITER[stufe]} ({d} док.)")
        stufe, d_fail, n_ohne = 0, None, 0
        start("обратно на полный батч", LEITER[0])
        n_err = fehler()
        continue
    g = gpus()
    heiss = [x for x in g if x[1] >= A.temp_limit or x[2] >= A.mem_limit]
    if heiss and pids()[1]:
        stopp("ПОРОГ GPU: " + ", ".join(f"GPU{i} {t} °C {m} MiB" for i, t, m in g))
        ev(f"пауза {COOL} с, GPU {gpus()}")
        time.sleep(COOL)
        start("после остывания", LEITER[stufe])
        continue
    e = fehler()
    try:
        still = (time.time() - os.path.getmtime(A.load_log)) / 60
    except FileNotFoundError:
        still = 0
    chain_tot = not pids()[0]
    if e > n_err or still > A.hang_min or chain_tot:
        grund = "Traceback в журнале загрузки" if e > n_err else ("цепочка завершилась без маркера" if chain_tot else f"журнал загрузки молчит {still:.0f} мин")
        if d_fail == d:
            n_ohne += 1
            stufe = min(stufe + 1, len(LEITER) - 1)
        else:
            n_ohne = 1
        d_fail = d
        # 22.10.2026 (W0: цепочка exakt упала 5 раз одной ошибкой «no results to fetch», СТОП не замечен 1 ч 17 мин): одна и та же
        # ошибка (не нехватка памяти GPU) два раза подряд -- перезапуск не поможет, сразу СТОП
        fz = fehlerzeile()
        gleich = e > n_err and fz and fz == getattr(fehlerzeile, "vorher", None) and "out of memory" not in fz
        fehlerzeile.vorher = fz if e > n_err else None
        if gleich:
            stopp(grund + f"; та же ошибка дважды: {fz[:150]}")
            with open(A.chain_log, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%F %T} сторож: СТОП -- та же ошибка дважды подряд, нужен разбор: {fz[:150]}\n")
            ev("СТОП")
            break
        if n_ohne >= 5:
            stopp(grund + "; 5 сбоев без новых документов")
            with open(A.chain_log, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%F %T} сторож: СТОП -- 5 сбоев подряд без прогресса, нужен разбор\n")
            ev("СТОП")
            break
        stopp(grund)
        start(grund, LEITER[stufe])
        n_err = fehler()
