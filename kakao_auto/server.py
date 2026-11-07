"""kakaotalk-mcp(고정 커밋) 위에 '창 자동 열기' 읽기를 덧붙인 로컬 MCP 서버.

원본 kakao_read_messages / kakao_extract_links 는 채팅방 창이 이미 떠 있어야만 동작한다.
여기서는 같은 이름의 도구를 바꿔 끼워서:
  1) 카카오톡 상태 확인 (미설치 / 미실행 / 로그인 전 / 잠금) → 상황별 안내 메시지
  2) 방 창이 없으면 메인창(최소화·트레이 상태 포함)을 복원 → 채팅 탭 → 검색 → 방 열기
  3) 방 이름이 정확하지 않으면 읽지 않고 비슷한 방 이름을 추천 (suggestions)
  4) Ctrl+A / Ctrl+C 로 읽기 (복사 실패 시 예전 클립보드 내용을 대화로 착각하지 않도록 비운 뒤 읽음)
  5) 직접 연 창은 닫고, 메인창도 원래 상태(최소화 등)로 되돌림, 사용자 클립보드 복원
키 입력(Enter, Ctrl+A/C)은 대상 창이 실제 전경일 때만 보낸다. 다른 창(터미널 등)에 입력이 새지 않게 하기 위함.
메시지 전송 계열 도구는 원본 그대로 둔다.

읽은 방 이름은 업무데이터/카카오톡/방목록.json (git 제외 폴더)에 기억해 두고 추천에 쓴다.

실행: uv run --no-project --with git+https://github.com/kronenz/kakaotalk-mcp@<PIN> --with "mcp<2" python kakao_auto/server.py
"""
import ctypes
import difflib
import json
import os
import re
import threading
import time
import winreg
from pathlib import Path
from typing import Dict, List, Optional

import win32clipboard
import win32con
import win32gui
import win32process

from kakao_mcp import config, controller, parser
from kakao_mcp.server import app

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_lock = threading.Lock()  # 동시에 두 번 창을 조작하지 않도록

VK_2 = 0x32
VK_F = 0x46
OPEN_POLL_SEC = 4.0

DATA_DIR = Path(__file__).resolve().parent.parent / "업무데이터" / "카카오톡"
ROOMS_FILE = DATA_DIR / "방목록.json"

DOWNLOAD_URL = "https://www.kakaocorp.com/page/service/service/KakaoTalk"
ASK_USER_HINT = ("방 이름이 정확하지 않습니다. suggestions 중 어느 방인지 사용자에게 확인한 뒤 "
                 "그 이름 그대로 다시 호출하세요.")


def _log(msg: str):
    controller._log(f"[auto] {msg}")


# ---------------------------------------------------------------- 카카오톡 상태

def _installed_exe() -> Optional[str]:
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\KakaoTalk"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\KakaoTalk"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\KakaoTalk"),
    ]
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as k:
                icon = winreg.QueryValueEx(k, "DisplayIcon")[0].split(",")[0].strip('"')
                if os.path.isfile(icon):
                    return icon
        except OSError:
            pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            p = os.path.join(base, "Kakao", "KakaoTalk", "KakaoTalk.exe")
            if os.path.isfile(p):
                return p
    return None


def _process_running() -> bool:
    for pid in win32process.EnumProcesses():
        h = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(260)
            n = ctypes.c_ulong(260)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                if buf.value.lower().endswith("\\kakaotalk.exe"):
                    return True
        finally:
            _kernel32.CloseHandle(h)
    return False


def _child_view_shown(main_hwnd: int, name: str) -> bool:
    """메인창 바로 아래 view 의 WS_VISIBLE 스타일 (메인창이 최소화·숨김이어도 판별 가능)."""
    found = []

    def cb(h, _):
        if win32gui.GetParent(h) == main_hwnd and win32gui.GetWindowText(h).startswith(name):
            found.append(bool(win32gui.GetWindowLong(h, win32con.GWL_STYLE) & win32con.WS_VISIBLE))
            return False
        return True
    try:
        win32gui.EnumChildWindows(main_hwnd, cb, None)
    except Exception:
        pass
    return bool(found and found[0])


def kakao_status() -> Dict:
    """state: ready / locked / login_required / not_running / not_installed + 안내문(guide)."""
    main = controller.is_kakaotalk_running()
    if main["running"]:
        h = main["hwnd"]
        if _child_view_shown(h, "LockModeView"):
            return {"state": "locked", "hwnd": h,
                    "guide": "카카오톡이 잠금 모드입니다. 카카오톡 창에서 잠금(비밀번호)을 해제한 뒤 다시 요청해 주세요."}
        if _child_view_shown(h, "OnlineMainView"):
            return {"state": "ready", "hwnd": h, "guide": "카카오톡이 로그인된 상태입니다."}
        return {"state": "login_required", "hwnd": h,
                "guide": "카카오톡이 로그인 화면에 있습니다. 카카오톡 PC에 로그인한 뒤 다시 요청해 주세요."}
    exe = _installed_exe()
    if _process_running():
        return {"state": "login_required", "hwnd": None,
                "guide": "카카오톡이 실행 중이지만 메인창이 아직 없습니다(시작 중이거나 로그인 전). "
                         "작업 표시줄 오른쪽 아래 트레이의 카카오톡 아이콘을 눌러 로그인한 뒤 다시 요청해 주세요."}
    if exe:
        return {"state": "not_running", "hwnd": None, "exe": exe,
                "guide": f"카카오톡 PC가 실행되어 있지 않습니다. 카카오톡을 실행하고 로그인한 뒤 다시 요청해 주세요. (설치 위치: {exe})"}
    return {"state": "not_installed", "hwnd": None,
            "guide": f"이 PC에 카카오톡 PC 버전이 설치되어 있지 않습니다. {DOWNLOAD_URL} 에서 Windows용을 설치하고 "
                     "로그인한 뒤 다시 요청해 주세요."}


# ---------------------------------------------------------------- 방 이름 기억·추천

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _jamo(s: str) -> str:
    """한글을 자모로 풀어 오타(우채국↔우체국)도 비슷하게 잡히게 한다."""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out += [_CHO[c // 588], _JUNG[(c % 588) // 28]]
            if c % 28:
                out.append(_JONG[c % 28])
        else:
            out.append(ch)
    return "".join(out)


def _load_known() -> List[str]:
    try:
        return [n for n in json.loads(ROOMS_FILE.read_text(encoding="utf-8")) if isinstance(n, str)]
    except Exception:
        return []


def _remember(title: str):
    if not title:
        return
    names = _load_known()
    if title in names:
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ROOMS_FILE.write_text(json.dumps(sorted(names + [title]), ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except Exception as e:
        _log(f"room list save failed: {e}")


def _candidates(extra=()) -> List[str]:
    names = list(extra) + _load_known() + [w["title"] for w in controller.list_chat_windows()]
    try:  # 정리 폴더 이름(업무데이터/카카오톡/<방>)도 후보로
        names += [p.name for p in DATA_DIR.iterdir() if p.is_dir()]
    except Exception:
        pass
    seen, out = set(), []
    for n in names:
        if n and _norm(n) not in seen:
            seen.add(_norm(n))
            out.append(n)
    return out


def suggest_rooms(query: str, extra=(), limit: int = 5) -> List[str]:
    q = _norm(query)
    if not q:
        return []
    scored = []
    for n in _candidates(extra):
        nn = _norm(n)
        if q in nn or nn in q:
            score = 0.9 + 0.1 * min(len(q), len(nn)) / max(len(q), len(nn))
        else:
            score = difflib.SequenceMatcher(None, _jamo(q), _jamo(nn)).ratio()
        if score >= 0.55:
            scored.append((score, n))
    return [n for _, n in sorted(scored, key=lambda x: -x[0])[:limit]]


# ---------------------------------------------------------------- 창 보조

def _is_foreground(hwnd: int, wait: float = 0.6) -> bool:
    end = time.time() + wait
    while time.time() < end:
        if _user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return _user32.GetForegroundWindow() == hwnd


def _focus_hwnd(hwnd: int) -> Optional[int]:
    """hwnd 가 속한 스레드에서 현재 키보드 포커스를 가진 컨트롤."""
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                    ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                    ("rcCaret", ctypes.c_long * 4)]
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    tid = _user32.GetWindowThreadProcessId(hwnd, None)
    if not _user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return None
    return info.hwndFocus or None


def _visible_search_edit(chat_list_view: int) -> Optional[int]:
    found = []

    def cb(h, _):
        if (win32gui.GetClassName(h) == "Edit" and win32gui.IsWindowVisible(h)
                and win32gui.GetParent(h) == chat_list_view):
            found.append(h)
            return False
        return True
    try:
        win32gui.EnumChildWindows(chat_list_view, cb, None)
    except Exception:
        pass
    return found[0] if found else None


def _set_focus(owner_hwnd: int, target: int):
    me = _kernel32.GetCurrentThreadId()
    tid = _user32.GetWindowThreadProcessId(owner_hwnd, None)
    _user32.AttachThreadInput(me, tid, True)
    try:
        _user32.SetFocus(target)
    finally:
        _user32.AttachThreadInput(me, tid, False)
    time.sleep(0.1)


def _clipboard_get() -> Optional[str]:
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _clipboard_set(text: Optional[str]):
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            if text is not None:
                win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        _log(f"clipboard set failed: {e}")


def _find_room(room_name: str) -> Optional[Dict]:
    """열린 창 중 이름이 같은 방 (띄어쓰기·대소문자 차이만 허용, 부분일치는 다른 방일 수 있어 제외)."""
    hwnd = controller.find_chat_window(room_name)
    if hwnd:
        return {"hwnd": hwnd, "title": room_name}
    key = _norm(room_name)
    for w in controller.list_chat_windows():
        if _norm(w["title"]) == key:
            return w
    return None


# ---------------------------------------------------------------- 방 열기

def _open_room_via_search(room_name: str, main_hwnd: int) -> Dict:
    before = {w["hwnd"] for w in controller.list_chat_windows()}
    controller.bring_window_to_front(main_hwnd)
    if not _is_foreground(main_hwnd, 1.5):
        return {"success": False, "error": "카카오톡 메인창을 앞으로 가져오지 못했습니다(다른 창이 포커스를 잡고 있음). 잠시 후 다시 요청해 주세요."}

    # 채팅 탭으로 전환 후 검색창 열기
    controller._send_ctrl_key_combo(VK_2)
    time.sleep(0.25)
    controller._send_ctrl_key_combo(VK_F)
    time.sleep(config.SEARCH_ACTIVATE_WAIT_SEC)

    chat_list_view = controller._find_chat_list_view(main_hwnd)
    edit = _visible_search_edit(chat_list_view) if chat_list_view else None
    if not edit:
        return {"success": False, "error": "카카오톡 채팅 목록의 검색창을 찾지 못했습니다. 카카오톡 창이 정상 화면인지 확인해 주세요."}
    # Ctrl+F 후에도 포커스가 목록에 남는 경우가 있음 → 그대로 Enter 하면 선택된 다른 방이 열림
    _set_focus(main_hwnd, edit)
    if _focus_hwnd(main_hwnd) != edit:
        return {"success": False, "error": "검색창에 포커스를 줄 수 없어 중단했습니다"}

    EM_SETSEL, WM_CLEAR = 0x00B1, 0x0303
    win32gui.SendMessage(edit, EM_SETSEL, 0, -1)
    win32gui.SendMessage(edit, WM_CLEAR, 0, 0)
    for ch in room_name:
        win32gui.SendMessage(edit, config.WM_CHAR, ord(ch), 0)
        time.sleep(config.SEARCH_CHAR_INTERVAL_SEC)
    time.sleep(config.SEARCH_RESULTS_WAIT_SEC)

    if not _is_foreground(main_hwnd) or _focus_hwnd(main_hwnd) != edit:
        return {"success": False, "error": "검색 도중 포커스가 바뀌어 중단했습니다"}
    _user32.keybd_event(config.VK_RETURN, 0, 0, 0)
    _user32.keybd_event(config.VK_RETURN, 0, config.KEYEVENTF_KEYUP, 0)

    end = time.time() + OPEN_POLL_SEC
    new: List[Dict] = []
    while time.time() < end:
        time.sleep(0.2)
        hit = _find_room(room_name)
        if hit:
            return {"success": True, "hwnd": hit["hwnd"], "title": hit["title"],
                    "opened_by_us": hit["hwnd"] not in before}
        new = [w for w in controller.list_chat_windows() if w["hwnd"] not in before]
        if new:
            time.sleep(0.3)  # 제목이 늦게 붙는 경우 한 번 더 확인
            hit = _find_room(room_name)
            if hit:
                return {"success": True, "hwnd": hit["hwnd"], "title": hit["title"],
                        "opened_by_us": hit["hwnd"] not in before}
            new = [w for w in controller.list_chat_windows() if w["hwnd"] not in before]
            break

    # 검색 1순위가 다른 이름의 방이면 읽지 않고 닫은 뒤 추천으로 돌려줌 (엉뚱한 방을 읽지 않음)
    opened = [w["title"] for w in new if w["title"]]
    for w in new:
        win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
    for t in opened:
        _remember(t)
    suggestions = suggest_rooms(room_name, extra=opened)
    for t in reversed(opened):  # 카카오톡 검색이 실제로 찾은 방을 맨 앞에
        if t in suggestions:
            suggestions.remove(t)
        suggestions.insert(0, t)
    if suggestions:
        return {"success": False, "suggestions": suggestions[:5],
                "error": f"'{room_name}' 과(와) 이름이 정확히 같은 방이 없습니다. 비슷한 방: "
                         + ", ".join(f"'{s}'" for s in suggestions[:5])}
    return {"success": False, "suggestions": [],
            "error": f"'{room_name}' 방을 카카오톡 검색에서 찾지 못했고 비슷한 이름도 없습니다. "
                     "카카오톡 채팅 목록에 보이는 방 이름을 알려주세요."}


# ---------------------------------------------------------------- 읽기

def _copy_room_text(hwnd: int) -> Dict:
    list_hwnd = controller.find_child_window_recursive(hwnd, config.KAKAO_LIST_CONTROL_CLASS)
    if not list_hwnd:
        return {"success": False, "error": "채팅방 메시지 목록을 찾지 못했습니다"}

    controller.bring_window_to_front(hwnd)
    if not _is_foreground(hwnd, 1.5):
        return {"success": False, "error": "채팅방 창을 앞으로 가져오지 못했습니다"}

    # 원본과 같은 방식(목록 클릭으로 포커스)
    l, t, r, b = win32gui.GetWindowRect(list_hwnd)
    _user32.SetCursorPos((l + r) // 2, (t + b) // 2)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)
    _user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(config.EDIT_CLICK_WAIT_SEC)

    if not _is_foreground(hwnd):
        return {"success": False, "error": "클릭 후 다른 창(사진 뷰어 등)이 앞에 와서 중단했습니다. 다시 요청해 주세요."}

    _clipboard_set(None)  # 복사 실패 시 예전 내용을 대화로 오인하지 않도록
    controller._send_ctrl_key_combo(config.VK_A)
    time.sleep(config.KEY_COMBO_WAIT_SEC)
    controller._send_ctrl_key_combo(config.VK_C)

    text = ""
    for _ in range(15):
        time.sleep(0.1)
        text = _clipboard_get() or ""
        if text:
            break
    if not text:
        return {"success": False, "error": "대화 내용을 복사하지 못했습니다"}
    return {"success": True, "raw_text": text}


def read_room(room_name: str, close_after: bool = True) -> Dict:
    status = kakao_status()
    if status["state"] != "ready":
        return {"success": False, "error": status["guide"], "kakao_state": status["state"]}
    if not room_name.strip():
        return {"success": False, "error": "방 이름이 비어 있습니다", "suggestions": _candidates()[:10]}

    with _lock:
        main_hwnd = status["hwnd"]
        main_was_iconic = bool(_user32.IsIconic(main_hwnd))
        main_was_visible = bool(win32gui.IsWindowVisible(main_hwnd))
        prev_fg = _user32.GetForegroundWindow()
        saved_clip = _clipboard_get()

        opened_by_us = False
        hwnd = None
        try:
            hit = _find_room(room_name)
            if hit:
                hwnd, title = hit["hwnd"], hit["title"]
            else:
                res = _open_room_via_search(room_name, main_hwnd)
                if not res["success"]:
                    return res
                hwnd, title, opened_by_us = res["hwnd"], res["title"], res["opened_by_us"]
                time.sleep(0.4)  # 방 메시지가 그려질 시간
            res = _copy_room_text(hwnd)
            if res["success"]:
                _remember(title)
            return dict(res, opened_by_us=opened_by_us, title=title)
        finally:
            if opened_by_us and close_after and hwnd:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            if not main_was_visible:
                win32gui.ShowWindow(main_hwnd, win32con.SW_HIDE)
            elif main_was_iconic:
                win32gui.ShowWindow(main_hwnd, win32con.SW_MINIMIZE)
            _clipboard_set(saved_clip)
            if prev_fg:
                _user32.SetForegroundWindow(prev_fg)


def _fail(res: Dict) -> Dict:
    out = {"error": res["error"]}
    if "kakao_state" in res:
        out["kakao_state"] = res["kakao_state"]
    if res.get("suggestions") is not None:
        out["suggestions"] = res["suggestions"]
        if res["suggestions"]:
            out["hint"] = ASK_USER_HINT
    return out


# ---------------------------------------------------------------- 도구 교체·추가

for _name in ("kakao_read_messages", "kakao_extract_links", "kakao_health_check"):
    app._tool_manager._tools.pop(_name, None)


@app.tool()
def kakao_health_check() -> Dict:
    """Check KakaoTalk PC status: ready / locked / login_required / not_running / not_installed.
    'guide' is a Korean message to show the user explaining what to do when not ready."""
    try:
        s = kakao_status()
        return {"state": s["state"], "running": s["state"] in ("ready", "locked", "login_required"),
                "ready": s["state"] == "ready", "guide": s["guide"], "hwnd": s.get("hwnd")}
    except Exception as e:
        return {"error": f"Health check failed: {e}"}


@app.tool()
def kakao_suggest_rooms(query: str = "") -> Dict:
    """Suggest KakaoTalk room names similar to a vague/misspelled query, without touching the UI.
    Candidates come from rooms read before, open chat windows and saved data folders.
    Empty query lists all known rooms.

    Args:
        query: What the user called the room (e.g. '우체', '우채국').
    """
    try:
        rooms = suggest_rooms(query, limit=10) if query.strip() else _candidates()
        return {"query": query, "suggestions": rooms,
                "note": "카카오톡에 실제로 있는 방 중 아직 한 번도 읽지 않은 방은 목록에 없을 수 있습니다."}
    except Exception as e:
        return {"error": f"Failed to suggest rooms: {e}"}


@app.tool()
def kakao_read_messages(room_name: str, max_messages: int = 50, close_after: bool = True) -> Dict:
    """Read recent messages from a KakaoTalk chat room.
    The room window does NOT need to be open: if it isn't, the main window is restored,
    the room is found via the chat-list search and opened, read with Ctrl+A/Ctrl+C,
    then closed again (close_after) and the main window/clipboard are restored.
    If the name doesn't exactly match a room, nothing is read and 'suggestions' lists
    similar room names: ask the user which one they meant, then call again with that exact name.
    If KakaoTalk isn't installed/running/logged in/unlocked, 'error' is a Korean guide for the user.

    Args:
        room_name: Chat room name (spacing/case differences are tolerated).
        max_messages: Maximum number of recent messages to return (default 50).
        close_after: Close the room window afterwards if this call opened it (default True).
    """
    try:
        res = read_room(room_name, close_after)
        if not res["success"]:
            return _fail(res)
        parsed = parser.parse_chat_text(res["raw_text"])
        messages = parsed["messages"][-max_messages:] if max_messages > 0 else parsed["messages"]
        return {
            "message": f"Read {len(messages)} messages from '{res['title']}'"
                       + (" (window auto-opened)" if res.get("opened_by_us") else ""),
            "room_name": parsed["room_name"] or res["title"],
            "member_count": parsed["member_count"],
            "dates": parsed["dates"],
            "messages": messages,
        }
    except Exception as e:
        return {"error": f"Failed to read messages: {e}"}


@app.tool()
def kakao_extract_links(room_name: str, close_after: bool = True) -> Dict:
    """Extract all URLs from a KakaoTalk chat room. Opens the room automatically if needed.
    Same name matching, suggestions and status guidance as kakao_read_messages.

    Args:
        room_name: Chat room name (spacing/case differences are tolerated).
        close_after: Close the room window afterwards if this call opened it (default True).
    """
    try:
        res = read_room(room_name, close_after)
        if not res["success"]:
            return _fail(res)
        parsed = parser.parse_chat_text(res["raw_text"])
        urls = parser.extract_urls_from_messages(parsed["messages"])
        return {"message": f"Found {len(urls)} URL(s) in '{res['title']}'", "links": urls}
    except Exception as e:
        return {"error": f"Failed to extract links: {e}"}


if __name__ == "__main__":
    app.run(transport="stdio")
