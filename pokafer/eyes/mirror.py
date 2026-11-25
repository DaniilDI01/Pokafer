"""Зеркало глаз: «что видят глаза» — в разделяемую память, для страницы отладки.

Конвейер глаз пишет, процесс-зеркало (а через него — страница в браузере)
читает. Память с двумя слотами (seqlock): запись никогда не блокирует
конвейер, читатель всегда получает целое состояние. Публикация идёт ТОЛЬКО
пока есть подписчик: пока страницу отладки никто не открыл, конвейер ничего
не кодирует и не пишет. Падение зеркала или страницы конвейер не затрагивает:
он лишь пишет в память, которую некому читать; ошибки разделяемой памяти
публикатор глотает — отладка не должна ронять бой.

Заголовок ( little-endian): magic «PKEY», версия, № активного слота, число
подписчиков, № сообщения, длины и времена двух слотов. Каждое поле пишется
отдельно, чтобы публикатор и подписчик не затирали чужие поля. Сообщение:
4 байта длины JSON, затем JSON состояния и, если есть, JPEG кадра.
"""
from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path

_MAGIC = b"PKEY"
_HEADER_SIZE = 64
SLOT_BYTES = 512 * 1024          # JSON состояния + JPEG кадра 1280x720
_ACTIVE = struct.Struct("<B")    # смещение 5: № слота, в котором лежит актуальное сообщение
_SUBSCRIBERS = struct.Struct("<I")  # смещение 6: сколько страниц открыто
_SEQ = struct.Struct("<Q")        # смещение 10: № последнего сообщения
_LENGTHS = (struct.Struct("<I"), struct.Struct("<I"))   # смещения 18 и 22: длины слотов
_TIMES = (struct.Struct("<d"), struct.Struct("<d"))     # смещения 26 и 34: время публикации
SESSIONS_DIR = Path(__file__).resolve().parents[2] / "data" / "eyes" / "sessions"


def memory_name(session: str) -> str:
    """Имя разделяемой памяти сессии (одна на открытый стол)."""
    return f"pokafer_eyes_{session}"


def encode(state: dict, jpeg: bytes = b"") -> bytes:
    """Состояние (+ кадр) -> сообщение зеркала."""
    doc = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(doc).to_bytes(4, "little") + doc + jpeg


def decode(payload: bytes) -> tuple[dict, bytes]:
    """Сообщение зеркала -> (состояние, JPEG кадра или пусто)."""
    n = int.from_bytes(payload[:4], "little")
    return json.loads(payload[4:4 + n].decode("utf-8")), payload[4 + n:]


def _slot(index: int) -> int:
    return _HEADER_SIZE + index * SLOT_BYTES


class _Memory:
    """Доступ к блоку зеркала: создание (владелец) либо подключение."""

    def __init__(self, session: str, create: bool):
        from multiprocessing.shared_memory import SharedMemory
        name = memory_name(session)
        try:
            self._shm = SharedMemory(name, create=create, size=_HEADER_SIZE + 2 * SLOT_BYTES)
            owner = create
        except FileExistsError:
            self._shm = SharedMemory(name, create=False)
            owner = False
        self.owner = owner
        if owner:
            self._shm.buf[:4] = _MAGIC
            self._shm.buf[4] = 1
        elif bytes(self._shm.buf[:4]) != _MAGIC:
            self.close()
            raise RuntimeError(f"зеркало «{session}» создано другой версией Pokafer")

    @property
    def version(self) -> int:
        return self._shm.buf[4]

    @property
    def active(self) -> int:
        return _ACTIVE.unpack_from(self._shm.buf, 5)[0]

    @active.setter
    def active(self, value: int) -> None:
        _ACTIVE.pack_into(self._shm.buf, 5, value)

    @property
    def subscribers(self) -> int:
        return _SUBSCRIBERS.unpack_from(self._shm.buf, 6)[0]

    @subscribers.setter
    def subscribers(self, value: int) -> None:
        _SUBSCRIBERS.pack_into(self._shm.buf, 6, value)

    @property
    def seq(self) -> int:
        return _SEQ.unpack_from(self._shm.buf, 10)[0]

    @seq.setter
    def seq(self, value: int) -> None:
        _SEQ.pack_into(self._shm.buf, 10, value)

    def slot_length(self, index: int) -> int:
        return _LENGTHS[index].unpack_from(self._shm.buf, 18 + 4 * index)[0]

    def slot_time(self, index: int) -> float:
        return _TIMES[index].unpack_from(self._shm.buf, 26 + 8 * index)[0]

    def write(self, payload: bytes, index: int) -> None:
        self._shm.buf[_slot(index):_slot(index) + len(payload)] = payload
        _LENGTHS[index].pack_into(self._shm.buf, 18 + 4 * index, len(payload))
        _TIMES[index].pack_into(self._shm.buf, 26 + 8 * index, time.time())

    def read(self, index: int) -> bytes:
        return bytes(self._shm.buf[_slot(index):_slot(index) + self.slot_length(index)])

    def close(self) -> None:
        self._shm.close()

    def unlink(self) -> None:
        try:
            self._shm.unlink()
        except Exception:
            pass


class MirrorPublisher:
    """Сторона конвейера: publish(сообщение) — неблокирующе и только при подписчике."""

    def __init__(self, session: str, url: str = ""):
        self.session = session
        self._mem = _Memory(session, create=True)
        self._registry = SESSIONS_DIR / f"{session}.json"   # зеркало перечисляет сессии по нему
        self._registry.parent.mkdir(parents=True, exist_ok=True)
        self._registry.write_text(json.dumps({"url": url, "pid": os.getpid(), "started": time.time()}),
                                  encoding="utf-8")

    @property
    def subscribed(self) -> bool:
        """Открыта ли страница отладки (есть подписчик в зеркале)."""
        return self._mem.subscribers > 0

    @property
    def seq(self) -> int:
        return self._mem.seq

    def publish(self, payload: bytes) -> bool:
        """Записать сообщение для зеркала. False — никто не подписан, не влезло или не удалось."""
        try:
            mem = self._mem
            if mem.subscribers == 0 or len(payload) > SLOT_BYTES:
                return False
            target = 1 - mem.active
            mem.write(payload, target)
            mem.active = target
            mem.seq = mem.seq + 1
            return True
        except Exception:
            return False           # отладочное зеркало не должно ронять конвейер

    def close(self) -> None:
        try:
            self._registry.unlink(missing_ok=True)
            self._mem.close()
            self._mem.unlink()
        except Exception:
            pass


class MirrorSubscriber:
    """Сторона зеркала: poll() -> новое сообщение (целое) или None."""

    def __init__(self, session: str):
        self.session = session
        self._mem = _Memory(session, create=False)
        self._last = -1

    @property
    def age(self) -> float:
        """Сколько секунд назад конвейер опубликовал состояние."""
        if self._mem.seq == 0:
            return float("inf")
        return max(0.0, time.time() - max(self._mem.slot_time(0), self._mem.slot_time(1)))

    def subscribe(self, on: bool) -> None:
        self._mem.subscribers = max(0, self._mem.subscribers + (1 if on else -1))

    def poll(self) -> bytes | None:
        """Сообщение, опубликованное с прошлого poll; None — нового не было."""
        mem = self._mem
        for _ in range(4):
            seq, active = mem.seq, mem.active
            if seq == self._last or seq == 0:
                return None
            payload = mem.read(active)
            if mem.seq == seq and mem.active == active:   # за время чтения ничего не поменялось
                self._last = seq
                return payload
        return None

    def close(self) -> None:
        self._mem.close()


def sessions() -> list[dict]:
    """Живые сессии по реестру (файлы пишет MirrorPublisher; осиротевшие убираются)."""
    found = []
    if SESSIONS_DIR.is_dir():
        for path in sorted(SESSIONS_DIR.glob("*.json")):
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
                os.kill(int(info["pid"]), 0)          # процесс конвейера ещё жив?
            except (OSError, ValueError, KeyError):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            info["session"] = path.stem
            found.append(info)
    return found
