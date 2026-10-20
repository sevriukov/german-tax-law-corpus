"""Надёжный пул эмбеддингов на обеих GPU (18.10.2026, после ночного зависания 17.10: в пуле sentence-transformers 6.0 рабочий процесс
при CUDA OOM падает, а родитель вечно ждёт ответа в output_queue.get()).
Здесь: по процессу на cuda:0 и cuda:1; каждый кусок кодируется с батчем EMB_BATCH (64); при OOM -- empty_cache и повтор ЭТОГО куска
на 48 -> 32 -> 16, следующий кусок снова на 64 (правило Сергея 16.10: батч насовсем не снижать). Не вышло и на 16 -- ошибка
возвращается родителю, загрузчик выходит с ненулевым кодом. Родитель ждёт ответ не дольше TIMEOUT с и проверяет, живы ли рабочие.
Проверка: файл quellen_neu/emb_test_oom (или emb_test_hang) -- один искусственный OOM (зависание) на первом куске; файл удаляется."""
import multiprocessing as mp
import os
import queue
import time

import numpy as np

LOKAL = "./modelle/bge-m3"     # 18.10.2026: полная копия модели (ссылки на кэш + model.safetensors из refs/pr/130) — работает и без сети

LEITER = (48, 32, 16)
SEG = 256                      # текстов в одном куске
TIMEOUT = int(os.environ.get("EMB_TIMEOUT", "1800"))
TEST_DIR = "./quellen_neu"


def _worker(dev, inq, outq):
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    import torch
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(LOKAL if os.path.isdir(LOKAL) else "BAAI/bge-m3", device=dev, model_kwargs={"use_safetensors": True})
    outq.put(("bereit", dev, None))
    while True:
        job = inq.get()
        if job is None:
            return
        sid, texts, batch = job
        for b in (batch,) + tuple(x for x in LEITER if x < batch):
            try:
                for name in ("emb_test_oom", "emb_test_hang"):
                    f = f"{TEST_DIR}/{name}"
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                        except FileNotFoundError:
                            continue
                        print(f"ТЕСТ {name} на {dev}", flush=True)
                        if name == "emb_test_hang":
                            time.sleep(10 ** 7)
                        raise torch.OutOfMemoryError("искусственный OOM (проверка)")
                e = model.encode(texts, batch_size=b, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
                if b != batch:
                    print(f"OOM_RETRY_OK {dev}: кусок {sid} прошёл на батче {b}", flush=True)
                outq.put(("ok", sid, e))
                break
            except torch.OutOfMemoryError as ex:
                print(f"OOM {dev}: кусок {sid}, батч {b}: {str(ex)[:120]}", flush=True)
                torch.cuda.empty_cache()
        else:
            outq.put(("fehler", sid, f"OOM на {dev} даже на батче {LEITER[-1]}"))


class Pool:
    def __init__(self, devices=("cuda:0", "cuda:1")):
        ctx = mp.get_context("spawn")
        self.inq, self.outq = ctx.Queue(), ctx.Queue()
        self.procs = [ctx.Process(target=_worker, args=(d, self.inq, self.outq), daemon=True) for d in devices]
        for p in self.procs:
            p.start()
        for _ in self.procs:
            self._get("загрузка модели")

    def _get(self, was):
        t0 = time.time()
        while True:
            try:
                r = self.outq.get(timeout=30)
            except queue.Empty:
                tot = [p.pid for p in self.procs if not p.is_alive()]
                if tot:
                    raise RuntimeError(f"рабочий процесс эмбеддингов умер (PID {tot}) во время: {was}")
                if time.time() - t0 > TIMEOUT:
                    raise RuntimeError(f"нет ответа от GPU {TIMEOUT} с во время: {was}")
                continue
            if r[0] == "fehler":
                raise RuntimeError(r[2])
            return r

    def encode(self, texts, batch):
        order = np.argsort([-len(t) for t in texts])          # длинные к длинным: меньше пустого заполнения
        segs = [order[i:i + SEG] for i in range(0, len(order), SEG)]
        for sid, idx in enumerate(segs):
            self.inq.put((sid, [texts[i] for i in idx], batch))
        out = [None] * len(segs)
        for k in range(len(segs)):
            _, sid, e = self._get(f"кодирование {len(texts)} текстов")
            out[sid] = e
            if (k + 1) % 20 == 0:                                # признак жизни для сторожа (журнал не молчит)
                print(f"    кусков {k + 1}/{len(segs)}", flush=True)
        res = np.empty((len(texts), out[0].shape[1]), dtype=out[0].dtype)
        for idx, e in zip(segs, out):
            res[idx] = e
        return res

    def close(self):
        for _ in self.procs:
            self.inq.put(None)
        for p in self.procs:
            p.join(timeout=60)
            if p.is_alive():
                p.kill()
