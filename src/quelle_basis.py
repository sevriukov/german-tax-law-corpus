"""28.10: сверка «до» — нынешний quelle_lauf.eins по рабочей базе (только чтение), итог в файлы OUT; документы — те же, что в staging."""
import os, sys
from multiprocessing import Pool
sys.path.insert(0, "./quellen_neu/pruefung5")
import quelle_lauf as Q
Q.OUT = "./quellen_neu/vorlauf_fix/quelle_basis_tmp"
if __name__ == "__main__":
    os.makedirs(Q.OUT, exist_ok=True)
    for x in os.listdir(Q.OUT): os.remove(os.path.join(Q.OUT, x))
    ids = [int(x) for x in open(sys.argv[1]) if x.strip()]
    with Pool(24) as p:
        for k, _ in enumerate(p.imap_unordered(Q.eins, ids, chunksize=16)):
            if k % 25000 == 0: print(k, flush=True)
    print("BASIS_DONE", len(ids), flush=True)
