"""last_in 舰队面板 — dashboard 后端 API，挂载于 /api/plugins/last-in/。

口径与「按 token 阈值轮换 Bot Chat」的 cron 脚本完全一致（同名环境变量
ROTATE_TOKEN_THRESHOLD / ROTATE_EXCLUDE 可直接共用）：
- canonical Bot Chat：各 profile state.db sessions 表
  WHERE title='Bot Chat' AND archived=0 ORDER BY started_at DESC LIMIT 1
- last_in：该 sid 在 logs/agent.log* 里最后一笔 "API call #N:" 的 in=
  （全量输入 token，含缓存命中 ≈ 当前上下文实长）；多文件时新文件覆盖旧文件
- 忙碌：session_turn_leases 存在未过期行（expires_at > now 或 NULL）
- 阈值：250,000（ROTATE_TOKEN_THRESHOLD 环境变量可覆盖，与 cron 同名同义）
- 排除：default、gameplayer（LAST_IN_EXCLUDE 可覆盖，与 cron ROTATE_EXCLUDE 同义）

只读：state.db 以 mode=ro 打开，日志只读，不写任何文件。
无 .gz 轮转（本机实测），故不处理 gzip。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = __import__("logging").getLogger(__name__)

router = APIRouter()

THRESHOLD = int(os.environ.get("ROTATE_TOKEN_THRESHOLD", os.environ.get("LAST_IN_THRESHOLD", "250000")))
EXCLUDE = set(filter(None, os.environ.get(
    "LAST_IN_EXCLUDE", os.environ.get("ROTATE_EXCLUDE", "default,gameplayer")).split(",")))

PROFILES_ROOT = Path.home() / ".hermes" / "profiles"

_SID_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_CALL = re.compile(r"API call #\d+:")
_IN = re.compile(r"\bin=(\d+)\s+out=")

# 文件级解析缓存：{abs_path: (mtime_ns, size, {sid: (last_in, epoch|None)})}
_CACHE: dict[str, tuple[int, int, dict[str, tuple[int, Optional[float]]]]] = {}


def _canonical_sid(profile_dir: Path) -> Optional[str]:
    db = profile_dir / "state.db"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute(
                "SELECT id FROM sessions WHERE title='Bot Chat' AND archived=0 "
                "ORDER BY started_at DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _is_busy(profile_dir: Path) -> bool:
    db = profile_dir / "state.db"
    if not db.is_file():
        return False
    now = time.time()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            cols = [c[1] for c in con.execute("PRAGMA table_info(session_turn_leases)")]
            rows = con.execute("SELECT * FROM session_turn_leases").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    for r in rows:
        d = dict(zip(cols, r))
        exp = d.get("expires_at")
        if exp is None or exp > now:
            return True
    return False


def _line_ts_epoch(line: str) -> Optional[float]:
    m = _SID_ROW.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except ValueError:
        return None


def _parse_file(path: Path, sid: str) -> tuple[int, Optional[float]]:
    """该文件内此 sid 的最后一笔 (in=, 行时间戳 epoch)；无匹配 (0, None)。"""
    last_in, last_ts = 0, None
    try:
        with path.open("rt", errors="replace") as f:
            for line in f:
                if sid not in line or "API call" not in line:
                    continue
                m = _IN.search(line)
                if m:
                    last_in = int(m.group(1))
                    last_ts = _line_ts_epoch(line)
    except OSError:
        return last_in, last_ts
    return last_in, last_ts


def _last_in(profile_dir: Path, sid: str) -> tuple[int, Optional[float]]:
    """logs/agent.log* 全部文件按旧→新合并，新文件覆盖旧文件（与 cron 的
    reversed(sorted(glob)) 语义一致）。带 mtime/size 文件级缓存。"""
    logs_dir = profile_dir / "logs"
    if not logs_dir.is_dir():
        return 0, None
    files = sorted(logs_dir.glob("agent.log*"))  # 旧→新（'agent.log' 是 'agent.log.1' 的前缀，排前面）
    merged_in, merged_ts = 0, None
    for path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        cached = _CACHE.get(key)
        per_sid: dict[str, tuple[int, Optional[float]]]
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            per_sid = cached[2]
        else:
            val = _parse_file(path, sid)
            per_sid = {sid: val}
            # 只缓存小文件（<8MB），防极端日志膨胀内存
            if st.st_size < 8 * 1024 * 1024:
                _CACHE[key] = (st.st_mtime_ns, st.st_size, per_sid)
            elif key in _CACHE:
                _CACHE.pop(key, None)
        v, ts = per_sid.get(sid, (0, None))
        if v:
            merged_in, merged_ts = v, ts
    return merged_in, merged_ts


def collect_snapshot() -> dict[str, Any]:
    t0 = time.time()
    now = time.time()
    bots: list[dict[str, Any]] = []
    excluded: list[str] = []
    if PROFILES_ROOT.is_dir():
        for child in sorted(PROFILES_ROOT.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in EXCLUDE:
                excluded.append(child.name)
                continue
            sid = _canonical_sid(child)
            if sid is None:
                bots.append({"profile": child.name, "sid": None, "last_in": 0,
                             "busy": False, "last_call_at": None, "source": "no_bot_chat"})
                continue
            last_in, ts = _last_in(child, sid)
            bots.append({
                "profile": child.name, "sid": sid, "last_in": last_in,
                "busy": _is_busy(child), "last_call_at": ts,
                "source": "log" if last_in else "log_empty"})
    bots.sort(key=lambda b: (-b["last_in"], b["profile"]))
    return {
        "bots": bots,
        "threshold": THRESHOLD,
        "excluded": sorted(excluded),
        "count": len(bots),
        "over_threshold": sum(1 for b in bots if b["last_in"] >= THRESHOLD),
        "busy_count": sum(1 for b in bots if b["busy"]),
        "generated_at": now,
        "scan_ms": round((time.time() - t0) * 1000),
    }


@router.get("/snapshot")
def snapshot():
    try:
        return collect_snapshot()
    except Exception as exc:  # 面板数据面失败不 5xx 硬崩，给结构化错误
        log.exception("last-in snapshot failed")
        return JSONResponse(status_code=500, content={"detail": f"last-in snapshot failed: {exc}"})
