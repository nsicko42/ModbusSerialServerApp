import asyncio
import datetime
import logging
import threading
import tkinter as tk
import warnings
from typing import Any

warnings.simplefilter("ignore", DeprecationWarning)

from tkinter import ttk, scrolledtext, messagebox

import serial.tools.list_ports
from pymodbus.framer import FramerType
from pymodbus.server import ModbusSerialServer
from pymodbus.simulator import DataType, SimData, SimDevice
from pymodbus.simulator.simcore import SimCore

# ── 데이터 블록 표시 상수 ────────────────────────────────────────
REG_TOTAL     = 65536                             # 주소 1 ~ 65535 (0-indexed 0~65534)
COLS          = 10
ROWS          = (REG_TOTAL + COLS - 1) // COLS   # 6554 행
ADDR_W        = 72                                # 주소 열 너비 (px)
CELL_W        = 58                                # 값 셀 너비 (px)
CELL_H        = 20                                # 셀 높이 (px)
HEADER_H      = 20                                # 헤더 높이 (px)
CANVAS_DATA_H = ROWS * CELL_H                     # 데이터 영역 전체 높이
CANVAS_W      = ADDR_W + COLS * CELL_W            # 652 px

# 데이터 블록 내부 탭 (키, 표시 이름, Modbus FC)
_DB_TABS = [
    ("co", "Coil",             1),
    ("di", "Discrete Input",   2),
    ("ir", "Input Register",   4),
    ("hr", "Holding Register", 3),
]
_DB_KEYS = [k for k, _, _ in _DB_TABS]


class _SerialLogHandler(logging.Handler):
    # pymodbus DEBUG 로그에서 RX/TX 메시지를 캡처해 시리얼 로그 탭에 전달.

    def __init__(self, cb):
        super().__init__(level=logging.DEBUG)
        self._cb = cb  # Callable[[str, str], None]  – (direction, message)

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
            lo  = msg.lower()
            if "recv:" in lo:
                self._cb("RX", msg)
            elif "send:" in lo:
                self._cb("TX", msg)
        except Exception:
            pass


class ModbusServerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Modbus 시리얼 서버")
        self.root.geometry("820x580")
        self.root.minsize(820, 400)

        self.server       = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server_thread: threading.Thread | None = None
        self.running      = False
        self.context: list[SimDevice] | None = None

        # 4종 레지스터 캐시 (0-indexed, 캐시[0] = Modbus 주소 1)
        self._co: list[int] = [0] * REG_TOTAL
        self._di: list[int] = [0] * REG_TOTAL
        self._ir: list[int] = [0] * REG_TOTAL
        self._hr: list[int] = [0] * REG_TOTAL

        # 탭별 캔버스·헤더·가상스크롤 아이템
        self._cv:            dict[str, tk.Canvas]            = {}
        self._hdr_cv:        dict[str, tk.Canvas]            = {}
        self._visible_items: dict[str, dict[int, list[int]]] = {
            k: {} for k in _DB_KEYS
        }

        # 정지 신호용 asyncio.Event (None = 서버 비실행 중)
        self._stop_event: asyncio.Event | None = None

        # pymodbus RX/TX 로그 캡처 핸들러
        self._serial_log_handler = _SerialLogHandler(
            lambda d, m: self.root.after(0, lambda dd=d, mm=m: self.log_serial(dd, mm))
        )

        self._led_tx: tuple | None = None
        self._led_rx: tuple | None = None
        self._build_ui()

    # ── UI 구성 ──────────────────────────────────────────────────

    def _build_ui(self):
        # 상단 컨트롤 바 (탭 바깥 – 항상 표시)
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=(6, 0))

        self.start_btn = ttk.Button(bar, text="[ ▶ ]", command=self.start_server)
        self.start_btn.pack(side="left", padx=(0, 4))

        self.stop_btn = ttk.Button(bar, text="[ ■ ]", command=self.stop_server, state="disabled")
        self.stop_btn.pack(side="left")

        # 버튼 오른쪽: 프로그램 로그 표시 (info / 오류) – 1줄 Text (스크롤바 없음)
        self.info_text = tk.Text(bar, state="disabled", font=("Consolas", 8),
                                 height=2, wrap="none", relief="sunken", bd=1)
        self.info_text.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.info_text.tag_configure("error", foreground="red")
        self.info_text.tag_configure("info",  foreground="black")

        # TX / RX LED (Canvas 원)
        LED_R = 6   # 반지름 px
        LED_D = LED_R * 2
        for label, attr in (("TX", "_led_tx"), ("RX", "_led_rx")):
            frm = ttk.Frame(bar)
            frm.pack(side="left", padx=(6, 0))
            cv = tk.Canvas(frm, width=LED_D+2, height=LED_D+2,
                           highlightthickness=0)
            cv.pack()
            oid = cv.create_oval(0, 0, LED_D, LED_D, fill="#444444", outline="")
            ttk.Label(frm, text=label, font=("Consolas", 7)).pack()
            setattr(self, attr, (cv, oid))  # (canvas, oval_id)

        # 탭 (Notebook)
        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True, padx=8, pady=6)

        db_tab   = ttk.Frame(self._nb)
        cfg_tab  = ttk.Frame(self._nb)
        slog_tab = ttk.Frame(self._nb)

        self._nb.add(db_tab,   text="데이터 블록")
        self._nb.add(cfg_tab,  text="설정")
        self._nb.add(slog_tab, text="시리얼 로그")
        self._nb.select(0)

        self._build_datablock(db_tab)
        self._build_settings(cfg_tab)
        self._build_serial_log(slog_tab)

        # 외부 탭 전환 시 렌더링 갱신
        self._nb.bind("<<NotebookTabChanged>>", lambda _e: self._redraw())

        # 하단 서버 상태 표시줄 (1줄)
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief="sunken", anchor="w"
                  ).pack(fill="x", padx=8, pady=(0, 4))

        self.refresh_ports()

    # ── 데이터 블록 탭 ───────────────────────────────────────────

    def _build_datablock(self, parent: ttk.Frame):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        # 내부 탭 (Coil / Discrete Input / Input Register / Holding Register)
        self._db_nb = ttk.Notebook(parent)
        self._db_nb.grid(row=0, column=0, sticky="nsew")

        for key, label, _ in _DB_TABS:
            tab = ttk.Frame(self._db_nb)
            self._db_nb.add(tab, text=label)
            self._build_register_canvas(tab, key)

        self._db_nb.bind("<<NotebookTabChanged>>", lambda _e: self._redraw())

    def _build_register_canvas(self, parent: ttk.Frame, key: str):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        # 고정 헤더
        hdr = tk.Canvas(parent, height=HEADER_H,
                        bg="#b0b0b0", highlightthickness=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.configure(scrollregion=(0, 0, CANVAS_W, HEADER_H))
        self._hdr_cv[key] = hdr
        self._draw_header(hdr)

        # 데이터 캔버스
        cv = tk.Canvas(parent, bg="white", highlightthickness=0)
        vbar = ttk.Scrollbar(parent, orient="vertical",  command=cv.yview)
        hbar = ttk.Scrollbar(parent, orient="horizontal",
                              command=self._make_hscroll(key))
        cv.configure(
            yscrollcommand=vbar.set,
            xscrollcommand=hbar.set,
            scrollregion=(0, 0, CANVAS_W, CANVAS_DATA_H),
        )
        cv.grid(row=1, column=0, sticky="nsew")
        vbar.grid(row=1, column=1, sticky="ns")
        hbar.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._cv[key] = cv

        cv.bind("<Configure>",  lambda _e: self._redraw())
        cv.bind("<MouseWheel>", lambda e, k=key: self._on_wheel(e, k))

    def _make_hscroll(self, key: str):
        # 수평 스크롤 – 헤더·데이터 캔버스 동기화.
        def _hscroll(*args):
            self._cv[key].xview(*args)
            self._hdr_cv[key].xview(*args)
        return _hscroll

    def _draw_header(self, c: tk.Canvas):
        c.create_rectangle(0, 0, ADDR_W, HEADER_H,
                            fill="#909090", outline="#666")
        c.create_text(ADDR_W // 2, HEADER_H // 2,
                      text="주소", font=("Consolas", 8, "bold"), fill="white")
        for col in range(COLS):
            x0 = ADDR_W + col * CELL_W
            x1 = x0 + CELL_W
            c.create_rectangle(x0, 0, x1, HEADER_H,
                                fill="#909090", outline="#666")
            c.create_text((x0 + x1) // 2, HEADER_H // 2,
                          text=f"+{col}",
                          font=("Consolas", 8, "bold"), fill="white")

    def _active_db_key(self) -> str:
        try:
            idx = self._db_nb.index(self._db_nb.select())
            return _DB_KEYS[idx]
        except Exception:
            return "hr"

    def _data_cache(self, key: str) -> list[int]:
        return {"co": self._co, "di": self._di,
                "ir": self._ir, "hr": self._hr}[key]

    def _visible_row_range(self, key: str) -> tuple[int, int]:
        c = self._cv[key]
        y0, y1 = c.yview()
        first = max(0, int(y0 * CANVAS_DATA_H / CELL_H) - 1)
        last  = min(ROWS - 1, int(y1 * CANVAS_DATA_H / CELL_H) + 2)
        return first, last

    def _redraw(self, *_):
        # 활성 내부 탭의 가시 영역만 렌더링 (가상 스크롤).
        try:
            if self._nb.index(self._nb.select()) != 0:
                return
        except Exception:
            return

        key  = self._active_db_key()
        c    = self._cv[key]
        data = self._data_cache(key)
        vis  = self._visible_items[key]

        first, last = self._visible_row_range(key)

        # 화면 밖 행 삭제
        for row in list(vis):
            if row < first or row > last:
                for iid in vis.pop(row):
                    c.delete(iid)

        # 가시 행 생성 또는 값 갱신
        for row in range(first, last + 1):
            y0   = row * CELL_H
            y1   = y0 + CELL_H
            base = row * COLS   # 0-indexed 시작 주소

            if row not in vis:
                items: list[int] = []

                # 주소 셀  items[0]=rect  items[1]=text
                items.append(c.create_rectangle(
                    0, y0, ADDR_W, y1, fill="#f0f0f0", outline="#ccc"))
                items.append(c.create_text(
                    ADDR_W // 2, (y0 + y1) // 2,
                    text=str(base + 1), font=("Consolas", 8)))

                # 값 셀  items[2+col*2]=rect  items[3+col*2]=text
                for col in range(COLS):
                    idx = base + col
                    x0  = ADDR_W + col * CELL_W
                    x1  = x0 + CELL_W
                    valid = idx < REG_TOTAL
                    items.append(c.create_rectangle(
                        x0, y0, x1, y1,
                        fill="white" if valid else "#ececec",
                        outline="#ccc"))
                    items.append(c.create_text(
                        (x0 + x1) // 2, (y0 + y1) // 2,
                        text=str(data[idx]) if valid else "",
                        font=("Consolas", 8)))

                vis[row] = items

            else:
                items = vis[row]
                for col in range(COLS):
                    idx = base + col
                    if idx < REG_TOTAL:
                        c.itemconfig(items[3 + col * 2],
                                     text=str(data[idx]))

    def _on_wheel(self, event: tk.Event, key: str):
        self._cv[key].yview_scroll(-1 * (event.delta // 120), "units")
        self._redraw()

    # ── 설정 탭 ──────────────────────────────────────────────────

    def _build_settings(self, parent: ttk.Frame):
        # (widget, state_when_enabled) – 서버 실행 중에는 모두 disabled
        self._cfg_widgets: list[tuple[Any, str]] = []

        cfg = ttk.LabelFrame(parent, text="시리얼 포트 설정", padding=10)
        cfg.pack(padx=15, pady=15, fill="x")

        row = 0

        # 모드
        ttk.Label(cfg, text="모드:").grid(row=row, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="RTU")
        rb_rtu = ttk.Radiobutton(cfg, text="RTU",   variable=self.mode_var, value="RTU")
        rb_asc = ttk.Radiobutton(cfg, text="ASCII", variable=self.mode_var, value="ASCII")
        rb_rtu.grid(row=row, column=1, sticky="w")
        rb_asc.grid(row=row, column=2, sticky="w")
        self._cfg_widgets.extend([(rb_rtu, "normal"), (rb_asc, "normal")])
        row += 1

        # 포트
        ttk.Label(cfg, text="포트:").grid(row=row, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(cfg, textvariable=self.port_var, width=14)
        self.port_combo.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._cfg_widgets.append((self.port_combo, "normal"))
        row += 1

        # 보레이트
        ttk.Label(cfg, text="보레이트:").grid(row=row, column=0, sticky="w")
        self.baud_var = tk.StringVar(value="19200")
        cb_baud = ttk.Combobox(cfg, textvariable=self.baud_var, width=14,
                     values=["1200","2400","4800","9600",
                             "19200","38400","57600","115200"])
        cb_baud.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._cfg_widgets.append((cb_baud, "normal"))
        row += 1

        # 데이터 비트
        ttk.Label(cfg, text="데이터 비트:").grid(row=row, column=0, sticky="w")
        self.bytesize_var = tk.StringVar(value="8")
        cb_byte = ttk.Combobox(cfg, textvariable=self.bytesize_var, width=14,
                     values=["7", "8"])
        cb_byte.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._cfg_widgets.append((cb_byte, "normal"))
        row += 1

        # 패리티
        ttk.Label(cfg, text="패리티:").grid(row=row, column=0, sticky="w")
        self.parity_var = tk.StringVar(value="N - None")
        cb_par = ttk.Combobox(cfg, textvariable=self.parity_var, width=14,
                     values=["N - None", "E - Even", "O - Odd"], state="readonly")
        cb_par.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._cfg_widgets.append((cb_par, "readonly"))   # 복원 시 readonly 유지
        row += 1

        # 스톱 비트
        ttk.Label(cfg, text="스톱 비트:").grid(row=row, column=0, sticky="w")
        self.stopbits_var = tk.StringVar(value="1")
        cb_stop = ttk.Combobox(cfg, textvariable=self.stopbits_var, width=14,
                     values=["1", "1.5", "2"], state="readonly")
        cb_stop.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._cfg_widgets.append((cb_stop, "readonly"))  # 복원 시 readonly 유지
        row += 1

        # 슬레이브 ID
        ttk.Label(cfg, text="슬레이브 ID:").grid(row=row, column=0, sticky="w")
        self.slave_id_var = tk.StringVar(value="1")
        ent_sid = ttk.Entry(cfg, textvariable=self.slave_id_var, width=16)
        ent_sid.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._cfg_widgets.append((ent_sid, "normal"))
        row += 1

        refresh_btn = ttk.Button(cfg, text="포트 새로고침", command=self.refresh_ports)
        refresh_btn.grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._cfg_widgets.append((refresh_btn, "normal"))

    # ── 시리얼 로그 탭 (RX / TX) ─────────────────────────────────

    def _build_serial_log(self, parent: ttk.Frame):
        self.serial_log_text = scrolledtext.ScrolledText(
            parent, state="disabled", font=("Consolas", 9), wrap="none")
        self.serial_log_text.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.serial_log_text.tag_configure("rx", foreground="#0055AA")  # 파란색
        self.serial_log_text.tag_configure("tx", foreground="#AA4400")  # 주황색
        ttk.Button(parent, text="지우기",
                   command=self.clear_serial_log).pack(pady=(0, 6))


    # ── 유틸리티 ─────────────────────────────────────────────────

    def log(self, msg: str, level: str = "info"):
        # 프로그램 로그 표시줄에 메시지 추가.
        ts  = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        tag = level if level in ("info", "error") else "info"
        self.info_text.configure(state="normal")
        self.info_text.insert("end", f"\n[{ts}] {msg}", tag)
        self.info_text.see("end")
        self.info_text.configure(state="disabled")

    def log_serial(self, direction: str, msg: str):
        # 시리얼 로그 탭에 RX/TX 메시지 추가.
        ts  = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        tag = "rx" if direction == "RX" else "tx"
        self.serial_log_text.configure(state="normal")
        self.serial_log_text.insert("end", f"[{ts}] {direction}  {msg}\n", tag)
        self.serial_log_text.see("end")
        self.serial_log_text.configure(state="disabled")
        # 데이터 수신(RX) 시 TX LED, 송신(TX) 시 RX LED 점멸
        self._blink_led("TX" if direction == "RX" else "RX")

    def _blink_led(self, which: str, ms: int = 150):
        """TX 또는 RX LED를 ms동안 켜다 끄다."""
        led = self._led_tx if which == "TX" else self._led_rx
        if led is None:
            return
        cv, oid = led
        color = "#FF8800" if which == "TX" else "#00BB44"
        cv.itemconfig(oid, fill=color)
        self.root.after(ms, lambda: cv.itemconfig(oid, fill="#444444"))

    def clear_serial_log(self):
        self.serial_log_text.configure(state="normal")
        self.serial_log_text.delete("1.0", "end")
        self.serial_log_text.configure(state="disabled")

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            self.port_combo.current(0)
        else:
            self.port_var.set("")

    def _set_controls(self, server_running: bool):
        self.start_btn.config(state="disabled" if server_running else "normal")
        self.stop_btn.config(state="normal"   if server_running else "disabled")
        # 설정 위젯: 서버 실행 중 비활성화, 정지 시 원래 상태로 복원
        for w, enabled_state in self._cfg_widgets:
            try:
                w.configure(state="disabled" if server_running else enabled_state)
            except Exception:
                pass

    # ── 레지스터 폴링 (500ms 주기, 가시 영역만) ─────────────────

    def _poll_registers(self):
        if not self.running:
            return
        try:
            if self.server is not None:
                ctx = self.server.context   # ModbusServerContext | SimCore
                slave_id = int(self.slave_id_var.get())
                if not isinstance(ctx, SimCore) or slave_id not in ctx.devices:
                    raise LookupError
                rt = ctx.devices[slave_id]   # SimRuntime (타입 SimCore로 좁혀짐)
                key = self._active_db_key()
                block_key = {"co": "c", "di": "d", "hr": "h", "ir": "i"}[key]
                blk    = rt.block[block_key]  # (start_addr, count, values_list, ...)
                values = blk[2]               # 라이브 뮤터블 리스트
                first_row, last_row = self._visible_row_range(key)
                start_idx = first_row * COLS
                count     = min((last_row - first_row + 1) * COLS, REG_TOTAL - start_idx)
                cache = self._data_cache(key)
                if block_key in ("h", "i"):
                    # 레지스터: values[i] = Modbus 주소 i+1 = cache[i]
                    end = min(start_idx + count, len(values))
                    cache[start_idx:end] = values[start_idx:end]
                else:
                    # 코일/DI: 비트 패킹 — (values[addr//8] >> (addr%8)) & 1
                    for i in range(count):
                        addr     = start_idx + i + 1  # 1-based Modbus 주소
                        byte_idx = addr // 8
                        bit_idx  = addr % 8
                        cache[start_idx + i] = ((values[byte_idx] >> bit_idx) & 1 if byte_idx < len(values) else 0)
        except Exception:
            pass

        self._redraw()
        self.root.after(500, self._poll_registers)

    # ── 서버 시작 ─────────────────────────────────────────────────

    def start_server(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("오류", "COM 포트를 선택하세요.")
            return

        try:
            slave_id = int(self.slave_id_var.get())
            baudrate = int(self.baud_var.get())
            bytesize = int(self.bytesize_var.get())
            stopbits = float(self.stopbits_var.get())
            parity   = self.parity_var.get()[0]   # "N - None" → "N"
        except ValueError as e:
            messagebox.showerror("오류", f"설정값 오류: {e}")
            return

        framer = FramerType.ASCII if self.mode_var.get() == "ASCII" else FramerType.RTU

        self.context = [SimDevice(slave_id, simdata=(
            [SimData(1, values=[0] * 65535, datatype=DataType.BITS)],       # co
            [SimData(1, values=[0] * 65535, datatype=DataType.BITS)],       # di
            [SimData(1, values=[0] * 65535, datatype=DataType.REGISTERS)],  # hr
            [SimData(1, values=[0] * 65535, datatype=DataType.REGISTERS)],  # ir
        ))]

        self.server_thread = threading.Thread(
            target=self._run_loop,
            args=(port, baudrate, bytesize, parity, stopbits, framer),
            daemon=True,
        )
        self.running = True
        self._set_controls(True)
        self.server_thread.start()

        # pymodbus RX/TX 로그 캡처 시작
        pymod_log = logging.getLogger("pymodbus")
        pymod_log.setLevel(logging.DEBUG)
        if self._serial_log_handler not in pymod_log.handlers:
            pymod_log.addHandler(self._serial_log_handler)

        mode_str = self.mode_var.get()
        self.status_var.set(
            f"실행 중  —  {port} / {mode_str} / {baudrate} bps / Slave ID={slave_id}")
        self.log(f"서버 시작: {port}, {mode_str}, {baudrate}bps, "
                 f"bytesize={bytesize}, parity={parity}, "
                 f"stopbits={stopbits}, Slave ID={slave_id}")

        self.root.after(500, self._poll_registers)

    def _run_loop(self, port, baudrate, bytesize, parity, stopbits, framer):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve(port, baudrate, bytesize, parity, stopbits, framer))
        except Exception as e:
            self.root.after(0, lambda err=e: self.log(f"서버 오류: {err}", "error"))
        finally:
            # _serve() 안에서 shutdown()을 await 했으므로 여기서는 단순히 닫기만 함
            try:
                self.loop.close()
            except Exception:
                pass
            self.loop = None
            self._stop_event = None
            self.root.after(0, self._on_stopped)

    async def _serve(self, port, baudrate, bytesize, parity, stopbits, framer):
        # 포트를 열고 _stop_event 신호까지 대기, async 컨텍스트에서 shutdown 수행.
        # StartAsyncSerialServer 는 내부에서 serve_forever()를 await 하므로 절대 반환하지 않음.
        # → ModbusSerialServer + serve_forever(background=True) 를 사용해야 함.
        # serve_forever(background=True) 는 listen()으로 포트만 열고 즉시 반환한다.
        self._stop_event = asyncio.Event()
        assert self.context is not None
        last_err: Exception | None = None
        srv: ModbusSerialServer | None = None
        for attempt in range(5):
            try:
                srv = ModbusSerialServer(
                    context=self.context,
                    framer=framer,
                    port=port,
                    baudrate=baudrate,
                    bytesize=bytesize,
                    parity=parity,
                    stopbits=stopbits,
                )
                await srv.serve_forever(background=True)   # 포트 열고 즉시 반환
                break
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if "permission" in err_str or "could not start" in err_str:
                    await asyncio.sleep(0.5)
                else:
                    raise
        else:
            raise last_err  # type: ignore[misc]

        self.server = srv

        # 정지 신호 대기 (call_soon_threadsafe(_stop_event.set) 로 깨어남)
        await self._stop_event.wait()

        # 올바른 async 컨텍스트에서 shutdown:
        # shutdown() = serving.set_result(True) + close() → transport가 정상 해제됨
        try:
            await self.server.shutdown()
        except Exception:
            pass
        # close 콜백이 루프에서 실제로 실행될 시간 확보
        await asyncio.sleep(0.1)

    # ── 서버 정지 ─────────────────────────────────────────────────

    def stop_server(self):
        if not self.running:
            return
        if self._stop_event is None:
            # _serve() 코루틴이 아직 이벤트를 생성하지 않았으면 잠시 후 재시도
            self.root.after(50, self.stop_server)
            return
        if self.loop and not self.loop.is_closed():
            # 스레드 안전한 방법으로 asyncio.Event를 set (run_coroutine_threadsafe 불필요)
            self.loop.call_soon_threadsafe(self._stop_event.set)
        else:
            self._on_stopped()

    def _on_stopped(self):
        # 워커 스레드가 완전히 종료(COM 포트 OS 핸들 해제)될 때까지 폴링
        if self.server_thread and self.server_thread.is_alive():
            self.root.after(100, self._on_stopped)
            return
        # pymodbus 로그 핸들러 제거
        try:
            logging.getLogger("pymodbus").removeHandler(self._serial_log_handler)
        except Exception:
            pass
        self.running = False
        self.server  = None
        self._set_controls(False)
        self.status_var.set("정지됨")
        self.log("서버 정지.")

    # ── 창 닫기 ──────────────────────────────────────────────────

    def on_close(self):
        if self.running:
            self.stop_server()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ModbusServerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
